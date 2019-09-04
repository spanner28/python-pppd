import fcntl
import os
import sys
import signal
import re
import time
from threading  import Thread
import codecs

try:
    from queue import Queue, Empty
except ImportError:
    from Queue import Queue, Empty  # python 2.x

from subprocess import Popen, PIPE, STDOUT

ON_POSIX = 'posix' in sys.builtin_module_names

def enqueue_output(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)
    out.close()

__version__ = '1.0.5'

PPPD_RETURNCODES = {
    1:  'Fatal error occured',
    2:  'Error processing options',
    3:  'Not executed as root or setuid-root',
    4:  'No kernel support, PPP kernel driver not loaded',
    5:  'Received SIGINT, SIGTERM or SIGHUP',
    6:  'Modem could not be locked',
    7:  'Modem could not be opened',
    8:  'Connect script failed',
    9:  'pty argument command could not be run',
    10: 'PPP negotiation failed',
    11: 'Peer failed (or refused) to authenticate',
    12: 'The link was terminated because it was idle',
    13: 'The link was terminated because the connection time limit was reached',
    14: 'Callback negotiated',
    15: 'The link was terminated because the peer was not responding to echo reque               sts',
    16: 'The link was terminated by the modem hanging up',
    17: 'PPP negotiation failed because serial loopback was detected',
    18: 'Init script failed',
    19: 'Failed to authenticate to the peer',
    20: 'Failed to allocate PPP',
    21: 'CHAP authentication failed',
    22: 'Connection terminated',
}

class PPPConnectionError(Exception):
    def __init__(self, code, output=None):
        self.code = code
        self.message = PPPD_RETURNCODES.get(code, 'Undocumented error occured')
        self.output = output

        super(Exception, self).__init__(code, output)

    def __str__(self):
        return self.message

class PPPConnection:
    def __init__(self, *args, **kwargs):
        self.output = ''
        self._laddr = None
        self._raddr = None

        self.commands = []

        self.args = *args
        self.kwargs = **kwargs

        self.command()

    def command(self):
        if self.kwargs.pop('sudo', True):
            sudo_path = self.kwargs.pop('sudo_path', '/usr/bin/sudo')
            if not os.path.isfile(sudo_path) or not os.access(sudo_path, os.X_OK):
                raise IOError('%s not found' % sudo_path)
            self.commands.append(sudo_path)

        pppd_path = self.kwargs.pop('pppd_path', '/usr/sbin/pppd')
        if not os.path.isfile(pppd_path) or not os.access(pppd_path, os.X_OK):
            raise IOError('%s not found' % pppd_path)

        self.commands.append(pppd_path)

        for k,v in self.kwargs.items():
            self.commands.append(k)
            self.commands.append(v)
        self.commands.extend(self.args)
        self.commands.append('nodetach')

    def connect(self, *args, **kwargs):
        self.run()

    def disconnect(self):
        self.command()
        self.commands.append('disconnect')
        self.run()

        # try:
        #     if not self.connected():
        #         return
        # except PPPConnectionError:
        #     return
        #
        # self.proc = Popen(self.commands, stdout=PIPE, bufsize=1, close_fds=ON_POSIX)
        # q = Queue()
        # t = Thread(target=enqueue_output, args=(self.proc.stdout, q))
        # t.daemon = True # thread dies with the program
        # t.start()
        #
        # self.proc.send_signal(signal.SIGHUP)
        # self.proc.wait()

    def run(self):
        self.proc = Popen(self.commands, stdout=PIPE, bufsize=1, close_fds=ON_POSIX)
        q = Queue()
        t = Thread(target=enqueue_output, args=(self.proc.stdout, q))
        t.daemon = True # thread dies with the program
        t.start()

        while True:
            try:
                try:  self.line = q.get_nowait() # or q.get(timeout=.1)
                except Empty:
                    None
                else:
                    self.line = codecs.decode(str(self.line).encode('utf-8', errors='ignore'), errors='ignore')
                    self.output += self.line

            except IOError as e:
                if e.errno != 11:
                    raise
                time.sleep(1)
            if 'ip-up finished' in self.output:
                return
            if 'Couldn\'t allocate PPP' in self.output:
                raise PPPConnectionError(20, self.output)
            if 'CHAP authentication failed' in self.output:
                raise PPPConnectionError(21, self.output)
            if 'Connection terminated' in self.output:
                raise PPPConnectionError(22, self.output)
            elif self.proc.poll():
                raise PPPConnectionError(self.proc.returncode, self.output)

    def reconnect(self):
        self.disconnect()
        self.connect()

    def read(self):
        return self.output

    @property
    def laddr(self):
        if not self._laddr:
            try:
                self.output += self.proc.stdout.read()
            except IOError as e:
                if e.errno != 11:
                    raise
            result = re.search(r'local  IP address ([\d\.]+)', self.output)
            if result:
                self._laddr = result.group(1)

        return self._laddr

    @property
    def raddr(self):
        if not self._raddr:
            try:
                self.output += self.proc.stdout.read()
            except IOError as e:
                if e.errno != 11:
                    raise
            result = re.search(r'remote IP address ([\d\.]+)', self.output)
            if result:
                self._raddr = result.group(1)

        return self._raddr

    def connected(self):

        self.proc = Popen(self.commands, stdout=PIPE, bufsize=1, close_fds=ON_POSIX)
        q = Queue()
        t = Thread(target=enqueue_output, args=(self.proc.stdout, q))
        t.daemon = True # thread dies with the program
        t.start()

        if self.proc.poll():
            try:
                self.output += self.proc.stdout.read()
            except IOError as e:
                if e.errno != 11:
                    raise
            if self.proc.returncode not in [0, 5]:
                raise PPPConnectionError(proc.returncode, self.output)
            return False
        elif 'ip-up finished' in self.output:
            return True

        return False
