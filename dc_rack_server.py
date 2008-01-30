#!c:\python25\python.exe

# Copyright (C) 2007  Matthew Neeley
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from labrad import types, util
from labrad.server import LabradServer, setting
from labrad.errors import Error
from twisted.python import log
from twisted.internet import defer, reactor
from twisted.internet.defer import inlineCallbacks, returnValue

import re

class NoConnectionError(Error):
    """You need to connect first."""
    code = 2

class PreampServer(LabradServer):
    name = 'DC Rack'

    PossibleLinks = [{'Server': 'governator_serial_server',
                      'Port':   'COM3',
                      'Name':   'Gov Loopback'},
                     {'Server': 'twins_serial_server',
                      'Port':   'COM1',
                      'Name':   'Electronics Room'},
                     {'Server': 't1000_serial_server',
                      'Port':   'COM1',
                      'Name':   'DR Lab'},
                     {'Server': 'predator_serial_server',
                      'Port':   'COM1',
                      'Name':   'ADR Lab'}]

    @inlineCallbacks
    def initServer(self):
        self.Links = []
        yield self.findLinks()

    @inlineCallbacks
    def findLinks(self):
        cxn = self.client
        for S in self.PossibleLinks:
            if S['Server'] in cxn.servers:
                print 'Checking %s...' % S['Name']
                ser = cxn.servers[S['Server']]
                ports = (yield ser.list_serial_ports())[0]
                if S['Port'] in ports:
                    print '  Found %s on %s' % (S['Port'], S['Server'])
                    self.Links += [{'Server': ser,
                                    'ServerName': S['Server'],
                                    'Port': S['Port'],
                                    'Name': S['Name']}]
        print 'Server ready'


    @setting(10, 'Get Link List', returns=['*s'])
    def list_links(self, c):
        """Requests a list of available serial links (COM ports on servers) to talk to preamps
        """
        return [L['Name'] for L in self.Links]


    @setting(11, 'Connect', name=['s'], returns=['s'])
    def connect(self, c, name):
        """Opens the link to talk to preamp."""
        allLinks = [L['Name'] for L in self.Links]
        if name not in allLinks:
            raise Error("No link named '%s' could be found." % name)
        
        if 'Name' not in c:
            c['Name'] = ''
            c['Link'] = ''

        if c['Name'] == name:
            yield c['Link']
            return

        for L in self.Links:
            if L['Name'] == name:
                if 'Server' in c:
                    yield c['Server'].close()
                    del c['Server']
                    c['Link'] = ''
                try:
                    yield L['Server'].open(L['Port'])
                    c['Server'] = L['Server']
                    c['Link'] = L['Port'] + ' on ' + L['ServerName']
                    yield c['Server'].baudrate(115200L)
                except:
                    if 'Server' in c:
                        yield c['Server'].close()
                        del c['Server']
                    raise Exception(1, "Can't open port!")
        returnValue(c['Link'])


    @setting(12, 'Disconnect', returns=[''])
    def disconnect(self, c):
        """Closes the link to talk to preamp."""
        if 'Server' in c:
            yield c['Server'].close()
            del c['Server']
            c['Name'] = ''


    @setting(20, 'Select Card', data=['w'], returns=['w'])
    def select_card(self, c, data):
        """Sends a select card command."""
        server = self.getServer(c)
        yield server.write([long(data&63)])
        returnValue(long(data&63))


    def getServer(self, c):
        if 'Server' not in c:
            raise NoConnectionError()
        else:
            return c['Server']


    def doBusCmd(self, c, data, settings, keys=None):
        """Send out a command from a dictionary of possibilities."""
        server = self.getServer(c)

        if keys is None:
            keys = sorted(settings.keys())

        if data is None:
            returnValue(keys)

        if data not in settings:
            raise Error('Allowed commands: %s.' % ', '.join(keys))

        d = server.write([settings[data]])
        return d.addCallback(lambda r: data)


    @setting(30, 'Analog Bus', ID=['w'], channel=['s'],
                 returns=['s', '*s'])
    def abus(self, c, ID, channel=None):
        """Select channel for output to analog bus.

        Send ID only to see a list of available channels.
        """
        settings = [{'A': 80L, 'B': 81L, 'C': 82L, 'D': 83L},
                    {'A': 88L, 'B': 89L, 'C': 90L, 'D': 91L}][ID]
        return self.doBusCmd(c, channel, settings)


    @setting(35, 'Digital Bus', ID=['w'], channel=['s'],
                 returns=['s', '*s'])
    def dbus(self, c, ID, channel=None):
        """Select channel for output to digital bus.

        Send ID only to see a list of available channels.
        """
        settings = [{'trigA':  64L, 'trigB': 65L, 'trigC':  66L, 'trigD': 67L,
                     'dadata': 68L, 'done':  69L, 'strobe': 70L, 'clk':   71L},
                    {'FOoutA': 72L, 'FOoutB':  73L, 'FOoutC': 74L, 'FOoutD':  75L,
                     'dasyn':  76L, 'cardsel': 77L, 'Pbus0':  78L, 'Clockon': 79L}][ID]
        return self.doBusCmd(c, channel, settings)


    def cmdToList(self, data, regID):
        l = [(data >> 18) & 0x3f | 0x80,
             (data >> 12) & 0x3f | 0x80,
             (data >>  6) & 0x3f | 0x80,
              data        & 0x3f | 0x80,
             regID]
        return [long(n) for n in l]

    def tupleToCmd(self, data):
        return ((data[0] & 7) << 21) | \
               ((data[1] & 7) << 18) | \
               ((data[2] & 1) << 17) | \
                (data[3] & 0xFFFF)

    @setting(40, 'Register',
                 channel=['s'],
                 data=['w: Lowest 24 bits: Register content',
                       '(wwww): High Pass, Low Pass, Polarity, DAC'],
                 returns=['w'])
    def register(self, c, channel, data):
        """Sends a command to the specified register."""
        server = self.getServer(c)
        ID = {'A': 192, 'B': 193, 'C': 194, 'D': 195}[channel]
        if isinstance(data, tuple):
            data = self.tupleToCmd(data)
        else:
            data &= 0xFFFFFF
        yield server.write(self.cmdToList(data, ID))
        returnValue(data)


    @setting(50, 'Ident',
                 timeout=[': Use a read timeout of 1s',
                          'v[s]: Use this read timeout'],
                 returns=['s'])
    def ident(self, c, timeout=types.Value(1, 's')):
        """Sends an identification command."""
        server = self.getServer(c)
        p = server.packet()
        p.timeout()
        p.read()
        p.write([96L])
        p.timeout(timeout)
        p.read(1, key = 'ID')
        p.timeout()
        p.read(key = 'ID')
        try:
            res = yield p.send()
            yield ''.join(res['ID'])
        except:
            raise Exception('Ident error')


    @setting(60, 'LEDs',
                 data=['w: Lowest 3 bits: LED flags',
                       '(bbb): Status of BP LED, FP FOout flash, FP Reg. Load Flash'],
                 returns=['w'])
    def LEDs(self, c, data):
        """Sets LED status."""
        server = self.getServer(c)
        if isinstance(data, tuple):
            data = 224 + 4*data[0] + 2*data[1] + 1*data[2]
        else:
            data = 224 + (data & 7)
        yield server.write([data])
        returnValue(data & 7)


    @setting(70, 'Init DACs', returns=['w'])
    def InitDACs(self, c):
        """Initialize the DACs."""
        server = self.getServer(c)
        yield server.write([196])
        returnValue(196L)




__server__ = PreampServer()

if __name__ == '__main__':
    from labrad import util
    util.runServer(__server__)