import airhook
from twisted.internet.defer import Deferred
from twisted.protocols import basic
from bencode import bencode, bdecode
from twisted.internet import reactor
import time

import hash

KRPC_TIMEOUT = 30

KRPC_ERROR = 1
KRPC_ERROR_METHOD_UNKNOWN = 2
KRPC_ERROR_RECEIVED_UNKNOWN = 3
KRPC_ERROR_TIMEOUT = 4

class KRPC(basic.NetstringReceiver):
    noisy = 1
    def __init__(self):
        self.tids = {}

    def resetConnection(self):
        self.brokenPeer = 0
        self._readerState = basic.LENGTH
        self._readerLength = 0

    def stringReceived(self, str):
        # bdecode
        try:
            msg = bdecode(str)
        except Exception, e:
            if self.naisy:
                print "response decode error: " + `e`
            self.d.errback()
        else:
            # look at msg type
            if msg['typ']  == 'req':
                ilen = len(str)
                # if request
                #	tell factory to handle
                f = getattr(self.factory ,"krpc_" + msg['req'], None)
                if f and callable(f):
                    msg['arg']['_krpc_sender'] =  self.transport.addr
                    try:
                        ret = apply(f, (), msg['arg'])
                    except Exception, e:
                        ## send error
                        str = bencode({'tid':msg['tid'], 'typ':'err', 'err' :`e`})
                        olen = len(str)
                        self.sendString(str)
                    else:
                        if ret:
                            #	make response
                            str = bencode({'tid' : msg['tid'], 'typ' : 'rsp', 'rsp' : ret})
                        else:
                            str = bencode({'tid' : msg['tid'], 'typ' : 'rsp', 'rsp' : {}})
                        #	send response
                        olen = len(str)
                        self.sendString(str)

                else:
                    if self.noisy:
                        print "don't know about method %s" % msg['req']
                    # unknown method
                    str = bencode({'tid':msg['tid'], 'typ':'err', 'err' : KRPC_ERROR_METHOD_UNKNOWN})
                    olen = len(str)
                    self.sendString(str)
                if self.noisy:
                    print "%s %s >>> %s - %s %s %s" % (time.asctime(), self.transport.addr, self.factory.node.port, 
                                                    ilen, msg['req'], olen)
            elif msg['typ'] == 'rsp':
                # if response
                # 	lookup tid
                if self.tids.has_key(msg['tid']):
                    df = self.tids[msg['tid']]
                    # 	callback
                    del(self.tids[msg['tid']])
                    df.callback({'rsp' : msg['rsp'], '_krpc_sender': self.transport.addr})
                # no tid, this transaction timed out already...
            elif msg['typ'] == 'err':
                # if error
                # 	lookup tid
                df = self.tids[msg['tid']]
                # 	callback
                df.errback(msg['err'])
                del(self.tids[msg['tid']])
            else:
                # unknown message type
                df = self.tids[msg['tid']]
                # 	callback
                df.errback(KRPC_ERROR_RECEIVED_UNKNOWN)
                del(self.tids[msg['tid']])
                
    def sendRequest(self, method, args):
        # make message
        # send it
        msg = {'tid' : hash.newID(), 'typ' : 'req',  'req' : method, 'arg' : args}
        str = bencode(msg)
        d = Deferred()
        self.tids[msg['tid']] = d
        def timeOut(tids = self.tids, id = msg['tid']):
            if tids.has_key(id):
                df = tids[id]
                del(tids[id])
                print ">>>>>> KRPC_ERROR_TIMEOUT"
                df.errback(KRPC_ERROR_TIMEOUT)
        reactor.callLater(KRPC_TIMEOUT, timeOut)
        self.sendString(str)
        return d
 