#!/usr/bin/env python
#
# remote_bitbang_serial_debug.py
#
# (C) 2016 Phillip Pearson <pp@myelin.co.nz
#
# Based on tcp_serial_redirect.py from PySerial
#
# (C) 2002-2015 Chris Liechti <cliechti@gmx.net>
#
# SPDX-License-Identifier:    BSD-3-Clause

import sys
import socket
import serial
import serial.threaded
import time

SHOW_STATE_TRANSITIONS = 0
COLLECT_PACKETS = 1

RESET     = 0
IDLE      = 1
DRSELECT  = 2
DRCAPTURE = 3
DRSHIFT   = 4
DREXIT1   = 5
DRPAUSE   = 6
DREXIT2   = 7
DRUPDATE  = 8
IRSELECT  = 9
IRCAPTURE = 10
IRSHIFT   = 11
IREXIT1   = 12
IRPAUSE   = 13
IREXIT2   = 14
IRUPDATE  = 15

state_names = (
    "RESET", "IDLE",
    "DRSELECT", "DRCAPTURE", "DRSHIFT", "DREXIT1", "DRPAUSE", "DREXIT2", "DRUPDATE",
    "IRSELECT", "IRCAPTURE", "IRSHIFT", "IREXIT1", "IRPAUSE", "IREXIT2", "IRUPDATE"
)

class JTAGStateMachine:
    def __init__(self):
        self.state = RESET
        self.dr_in = ""
        self.ir_in = ""
        self.dr_out = ""
        self.dr_out_seen = ""
        self.ir_out = ""
        self.ir_out_seen = ""
        self.trst = -1
        self.srst = -1

    def update(self, tms, tdi):
        prev_state = self.state
        if self.state == RESET:
            print "TAP reset"
            self.state = RESET if tms else IDLE
        elif self.state == IDLE:
            self.state = DRSELECT if tms else IDLE
        elif self.state == DRSELECT:
            self.state = IRSELECT if tms else DRCAPTURE
        elif self.state == DRCAPTURE:
            #self.dr_out = " " + self.dr_out
            self.dr_out = ""
            self.state = DREXIT1 if tms else DRSHIFT
        elif self.state == DRSHIFT:
            self.state = DREXIT1 if tms else DRSHIFT
            #print "shift in %s to DR" % tdi
            self.dr_in = self.dr_in + '%d' % tdi
            if tms:
                print "%sexit DRSHIFT after clocking tdi = %s tdo = %s" % (self.reset_state(), self.dr_in, self.dr_out)
                #print "dr in  %s" % self.dr_in
                #self.dr_in += " "
                self.dr_in = ""
        elif self.state == DREXIT1:
            self.state = DRUPDATE if tms else DRPAUSE
        elif self.state == DRPAUSE:
            self.state = DREXIT2 if tms else DRPAUSE
        elif self.state == DREXIT2:
            self.state = DRUPDATE if tms else DRSHIFT
        elif self.state == DRUPDATE:
            self.state = DRSELECT if tms else IDLE
        elif self.state == IRSELECT:
            self.state = RESET if tms else IRCAPTURE
        elif self.state == IRCAPTURE:
            #self.ir_out = " " + self.ir_out
            self.ir_out = ""
            self.state = IREXIT1 if tms else IRSHIFT
        elif self.state == IRSHIFT:
            self.state = IREXIT1 if tms else IRSHIFT
            self.ir_in = self.ir_in + '%d' % tdi
            if tms:
                print "%sexit IRSHIFT after clocking tdi = %s tdo = %s" % (self.reset_state(), self.ir_in, self.ir_out)
                #print "ir in  %s" % self.ir_in
                #self.ir_in += " "
                self.ir_in = ""
        elif self.state == IREXIT1:
            self.state = IRUPDATE if tms else IRPAUSE
        elif self.state == IRPAUSE:
            self.state = IREXIT2 if tms else IRPAUSE
        elif self.state == IREXIT2:
            self.state = IRUPDATE if tms else IRSHIFT
        elif self.state == IRUPDATE:
            self.state = DRSELECT if tms else IDLE
        else:
            raise Exception("invalid state %d", self.state)
        if SHOW_STATE_TRANSITIONS and prev_state != self.state:
            print "%s -> %s" % (state_names[prev_state], state_names[self.state])

    def received(self, tdo):
        if self.state == DRSHIFT:
            if tdo not in self.dr_out_seen: self.dr_out_seen += tdo
            self.dr_out = tdo + self.dr_out
            #print "DR OUT %s" % (self.dr_out)
        elif self.state == IRSHIFT:
            if tdo not in self.ir_out_seen: self.ir_out_seen += tdo
            self.ir_out = tdo + self.ir_out
            #print "IR OUT %s" % (self.ir_out)
        else:
            print "read %s in state %s" % (tdo, state_names[self.state])

    def reset(self, trst, srst):
        if trst != self.trst:
            print "TEST RESET change: %d" % trst
            self.trst = trst
        if srst != self.srst:
            print "SYSTEM RESET change: %d" % srst
            self.srst = srst

    def reset_state(self):
        if self.srst:
            if self.trst:
                return "[SYS + TEST RESET] "
            else:
                return "[SYS RESET] "
        elif self.trst:
            return "[TEST RESET] "
        else:
            return ""

class SerialToNet(serial.threaded.Protocol):
    """serial->socket"""

    def __init__(self):
        self.socket = None

    def __call__(self):
        return self

    def data_received(self, data):
        if self.socket is not None:
            for c in data:
                jtag_state.received(c)
            self.socket.sendall(data)


if __name__ == '__main__':  # noqa
    import argparse

    parser = argparse.ArgumentParser(
        description='Simple Serial to Network (TCP/IP) redirector.',
        epilog="""\
NOTE: no security measures are implemented. Anyone can remotely connect
to this service over the network.

Only one connection at once is supported. When the connection is terminated
it waits for the next connect.
""")

    parser.add_argument(
        'SERIALPORT',
        help="serial port name")

    parser.add_argument(
        'BAUDRATE',
        type=int,
        nargs='?',
        help='set baud rate, default: %(default)s',
        default=9600)

    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='suppress non error messages',
        default=False)

    group = parser.add_argument_group('serial port')

    group.add_argument(
        "--parity",
        choices=['N', 'E', 'O', 'S', 'M'],
        type=lambda c: c.upper(),
        help="set parity, one of {N E O S M}, default: N",
        default='N')

    group.add_argument(
        '--rtscts',
        action='store_true',
        help='enable RTS/CTS flow control (default off)',
        default=False)

    group.add_argument(
        '--xonxoff',
        action='store_true',
        help='enable software flow control (default off)',
        default=False)

    group.add_argument(
        '--rts',
        type=int,
        help='set initial RTS line state (possible values: 0, 1)',
        default=None)

    group.add_argument(
        '--dtr',
        type=int,
        help='set initial DTR line state (possible values: 0, 1)',
        default=None)

    group = parser.add_argument_group('network settings')

    group.add_argument(
        '-P', '--localport',
        type=int,
        help='local TCP port',
        default=7777)

    args = parser.parse_args()

    # connect to serial port
    ser = serial.serial_for_url(args.SERIALPORT, do_not_open=True)
    ser.baudrate = args.BAUDRATE
    ser.parity = args.parity
    ser.rtscts = args.rtscts
    ser.xonxoff = args.xonxoff

    if args.rts is not None:
        ser.rts = args.rts

    if args.dtr is not None:
        ser.dtr = args.dtr

    if not args.quiet:
        sys.stderr.write(
            '--- TCP/IP to Serial redirect on {p.name}  {p.baudrate},{p.bytesize},{p.parity},{p.stopbits} ---\n'
            '--- type Ctrl-C / BREAK to quit\n'.format(p=ser))

    jtag_state = JTAGStateMachine()

    try:
        ser.open()
    except serial.SerialException as e:
        sys.stderr.write('Could not open serial port {}: {}\n'.format(ser.name, e))
        sys.exit(1)

    ser_to_net = SerialToNet()
    serial_worker = serial.threaded.ReaderThread(ser, ser_to_net)
    serial_worker.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('', args.localport))
    srv.listen(1)
    tms = tck = tdi = 0
    try:
        while True:
            sys.stderr.write('Waiting for connection on {}...\n'.format(args.localport))
            client_socket, addr = srv.accept()
            if COLLECT_PACKETS: client_socket.setblocking(0)
            sys.stderr.write('Connected by {}\n'.format(addr))
            try:
                ser_to_net.socket = client_socket
                # enter network <-> serial loop
                while True:
                    try:
                        if COLLECT_PACKETS:
                            data = ''
                            while 1:
                                try:
                                    data += client_socket.recv(1024)
                                except IOError, e:
                                    if e.errno == 35:
                                        break
                                    raise
                            if data == '':
                                # print "sleep"
                                time.sleep(0.001)
                                continue
                        else:
                            data = client_socket.recv(1024)
                            if not data: break
                        # print "received bytes %s" % `data`
                        # received bytes from openocd
                        ser.write(data)
                        for c in data:
                            # ser.write(c)
                            ptck, ptms, ptdi = (tck, tms, tdi)
                            if c == 'B':
                                print "LED ON"
                            elif c == 'b':
                                print "led off"
                            elif c == 'r':
                                jtag_state.reset(0, 0)
                            elif c == 's':
                                jtag_state.reset(0, 1)
                            elif c == 't':
                                jtag_state.reset(1, 0)
                            elif c == 'u':
                                jtag_state.reset(1, 1)
                            elif c == '0':
                                #print " tck   tms   tdi"
                                tck, tms, tdi = (0, 0, 0)
                            elif c == '1':
                                #print " tck   tms  *TDI*"
                                tck, tms, tdi = (0, 0, 1)
                            elif c == '2':
                                #print " tck  *TMS*  tdi"
                                tck, tms, tdi = (0, 1, 0)
                            elif c == '3':
                                #print " tck  *TMS* *TDI*"
                                tck, tms, tdi = (0, 1, 1)
                            elif c == '4':
                                #print "*TCK*  tms   tdi"
                                tck, tms, tdi = (1, 0, 0)
                            elif c == '5':
                                #print "*TCK*  tms  *TDI*"
                                tck, tms, tdi = (1, 0, 1)
                            elif c == '6':
                                #print "*TCK* *TMS*  tdi"
                                tck, tms, tdi = (1, 1, 0)
                            elif c == '7':
                                #print "*TCK* *TMS* *TDI*"
                                tck, tms, tdi = (1, 1, 1)
                            elif c == 'R':
                                if jtag_state.state not in (DRSHIFT, IRSHIFT):
                                    print "read during %s state" % state_names[jtag_state.state]
                            elif c == 'Q':
                                print "OpenOCD exit"
                                jtag_state = JTAGStateMachine()
                                print "\n" * 10
                            else:
                                print "unknown char %s" % `c`
                            if tck and not ptck:
                                if ptms != tms:
                                    print "updating tms on rising tck edge"
                                if ptdi != tdi:
                                    print "updating tdi on rising tck edge"
                                jtag_state.update(tms, tdi)

                    except socket.error as msg:
                        sys.stderr.write('ERROR: %s\n' % msg)
                        # probably got disconnected
                        break
            except socket.error as msg:
                sys.stderr.write('ERROR: {}\n'.format(msg))
            finally:
                ser_to_net.socket = None
                sys.stderr.write('Disconnected\n')
                client_socket.close()
    except KeyboardInterrupt:
        pass

    sys.stderr.write('\n--- exit ---\n')
    serial_worker.stop()
