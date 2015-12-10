# Copyright (c) 2009-2014 Upi Tamminen <desaster@gmail.com>
# See the COPYRIGHT file for more information

"""
This module contains ...
"""

import struct

import twisted
from twisted.conch import avatar
from twisted.conch.interfaces import IConchUser
from twisted.conch.ssh import userauth
from twisted.conch.ssh.common import NS, getNS
from twisted.internet import defer

from cowrie.core import credentials
from cowrie.core import auth


class HoneyPotSSHUserAuthServer(userauth.SSHUserAuthServer):
    """
    This contains modifications to the authentication system to do:
    * Login banners (like /etc/issue.net)
    * Anonymous authentication
    * Keyboard-interactive authentication (PAM)
    * IP based authentication
    """

    def serviceStarted(self):
        """
        """
        self.interfaceToMethod[credentials.IUsername] = 'none'
        self.interfaceToMethod[credentials.IUsernamePasswordIP] = 'password'
        self.interfaceToMethod[credentials.IPluggableAuthenticationModulesIP] = 'keyboard-interactive'
        self.bannerSent = False
        self._pamDeferred = None
        userauth.SSHUserAuthServer.serviceStarted(self)


    def sendBanner(self):
        """
        """
        if self.bannerSent:
            return
        self.bannerSent = True
        try:
            honeyfs = self.portal.realm.cfg.get('honeypot', 'contents_path')
            issuefile = honeyfs + "/etc/issue.net"
            data = file(issuefile).read()
        except IOError:
            return
        if not data or not len(data.strip()):
            return
        self.transport.sendPacket(
            userauth.MSG_USERAUTH_BANNER, NS(data) + NS('en'))


    def ssh_USERAUTH_REQUEST(self, packet):
        """
        """
        self.sendBanner()
        return userauth.SSHUserAuthServer.ssh_USERAUTH_REQUEST(self, packet)


    def auth_none(self, packet):
        """
        """
        c = credentials.Username(self.user)
        src_ip = self.transport.transport.getPeer().host
        return self.portal.login(c, src_ip, IConchUser)


    def auth_password(self, packet):
        """
        Overridden to pass src_ip to credentials.UsernamePasswordIP
        """
        password = getNS(packet[1:])[0]
        src_ip = self.transport.transport.getPeer().host
        c = credentials.UsernamePasswordIP(self.user, password, src_ip)
        return self.portal.login(c, src_ip,
            IConchUser).addErrback(self._ebPassword)


    def auth_keyboard_interactive(self, packet):
        """
        Keyboard interactive authentication.  No payload.  We create a
        PluggableAuthenticationModules credential and authenticate with our
        portal.

        Overridden to pass src_ip to credentials.PluggableAuthenticationModulesIP
        """
        if self._pamDeferred is not None:
            self.transport.sendDisconnect(
                    transport.DISCONNECT_PROTOCOL_ERROR,
                    "only one keyboard interactive attempt at a time")
            return defer.fail(error.IgnoreAuthentication())
        src_ip = self.transport.transport.getPeer().host
        c = credentials.PluggableAuthenticationModulesIP(self.user,
            self._pamConv, src_ip)
        return self.portal.login(c, src_ip,
            IConchUser).addErrback(self._ebPassword)


    def _pamConv(self, items):
        """
        Convert a list of PAM authentication questions into a
        MSG_USERAUTH_INFO_REQUEST.  Returns a Deferred that will be called
        back when the user has responses to the questions.

        @param items: a list of 2-tuples (message, kind).  We only care about
            kinds 1 (password) and 2 (text).
        @type items: C{list}
        @rtype: L{defer.Deferred}
        """
        resp = []
        for message, kind in items:
            if kind == 1: # Password
                resp.append((message, 0))
            elif kind == 2: # Text
                resp.append((message, 1))
            elif kind in (3, 4):
                return defer.fail(error.ConchError(
                    'cannot handle PAM 3 or 4 messages'))
            else:
                return defer.fail(error.ConchError(
                    'bad PAM auth kind %i' % (kind,)))
        packet = NS('') + NS('') + NS('')
        packet += struct.pack('>L', len(resp))
        for prompt, echo in resp:
            packet += NS(prompt)
            packet += chr(echo)
        self.transport.sendPacket(userauth.MSG_USERAUTH_INFO_REQUEST, packet)
        self._pamDeferred = defer.Deferred()
        return self._pamDeferred


    def ssh_USERAUTH_INFO_RESPONSE(self, packet):
        """
        The user has responded with answers to PAMs authentication questions.
        Parse the packet into a PAM response and callback self._pamDeferred.
        Payload::
            uint32 numer of responses
            string response 1
            ...
            string response n
        """
        d, self._pamDeferred = self._pamDeferred, None

        try:
            resp = []
            numResps = struct.unpack('>L', packet[:4])[0]
            packet = packet[4:]
            while len(resp) < numResps:
                response, packet = getNS(packet)
                resp.append((response, 0))
            if packet:
                raise error.ConchError(
                    "{:d} bytes of extra data".format(len(packet)))
        except:
            d.errback(failure.Failure())
        else:
            d.callback(resp)

