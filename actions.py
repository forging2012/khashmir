from time import time
from pickle import loads, dumps

from bsddb3 import db

from const import reactor

from hash import intify
from knode import KNode as Node
from ktable import KTable, K

# concurrent FIND_NODE/VALUE requests!
N = 3

class ActionBase:
    """ base class for some long running asynchronous proccesses like finding nodes or values """
    def __init__(self, table, target, callback):
	self.table = table
	self.target = target
	self.int = intify(target)
	self.found = {}
	self.queried = {}
	self.answered = {}
	self.callback = callback
	self.outstanding = 0
	self.finished = 0
	
	def sort(a, b, int=self.int):
	    """ this function is for sorting nodes relative to the ID we are looking for """
	    x, y = int ^ a.int, int ^ b.int
	    if x > y:
		return 1
	    elif x < y:
		return -1
	    return 0
	self.sort = sort
    
    def goWithNodes(self, t):
	pass
	
	

FIND_NODE_TIMEOUT = 15

class FindNode(ActionBase):
    """ find node action merits it's own class as it is a long running stateful process """
    def handleGotNodes(self, args):
	l, sender = args
	if self.finished or self.answered.has_key(sender['id']):
	    # a day late and a dollar short
	    return
	self.outstanding = self.outstanding - 1
	self.answered[sender['id']] = 1
	for node in l:
	    if not self.found.has_key(node['id']):
		n = Node(node['id'], node['host'], node['port'])
		self.found[n.id] = n
		self.table.insertNode(n)
	self.schedule()
		
    def schedule(self):
	"""
	    send messages to new peers, if necessary
	"""
	if self.finished:
	    return
	l = self.found.values()
	l.sort(self.sort)

	for node in l[:K]:
	    if node.id == self.target:
		self.finished=1
		return self.callback([node])
	    if not self.queried.has_key(node.id) and node.id != self.table.node.id:
		#xxxx t.timeout = time.time() + FIND_NODE_TIMEOUT
		df = node.findNode(self.target, self.table.node.senderDict())
		df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
		self.outstanding = self.outstanding + 1
		self.queried[node.id] = 1
	    if self.outstanding >= N:
		break
	assert(self.outstanding) >=0
	if self.outstanding == 0:
	    ## all done!!
	    self.finished=1
	    reactor.callFromThread(self.callback, l[:K])
	
    def defaultGotNodes(self, t):
	if self.finished:
	    return
	self.outstanding = self.outstanding - 1
	self.schedule()
	
	
    def goWithNodes(self, nodes):
	"""
	    this starts the process, our argument is a transaction with t.extras being our list of nodes
	    it's a transaction since we got called from the dispatcher
	"""
	for node in nodes:
	    if node.id == self.table.node.id:
		continue
	    self.found[node.id] = node
	    #xxx t.timeout = time.time() + FIND_NODE_TIMEOUT
	    df = node.findNode(self.target, self.table.node.senderDict())
	    df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
	    self.outstanding = self.outstanding + 1
	    self.queried[node.id] = 1
	if self.outstanding == 0:
	    self.callback(nodes)


GET_VALUE_TIMEOUT = 15
class GetValue(FindNode):
    """ get value task """
    def handleGotNodes(self, args):
	l, sender = args
	if self.finished or self.answered.has_key(sender['id']):
	    # a day late and a dollar short
	    return
	self.outstanding = self.outstanding - 1
	self.answered[sender['id']] = 1
	# go through nodes
	# if we have any closer than what we already got, query them
	if l.has_key('nodes'):
	    for node in l['nodes']:
		if not self.found.has_key(node['id']):
		    n = Node(node['id'], node['host'], node['port'])
		    self.found[n.id] = n
		    self.table.insertNode(n)
	elif l.has_key('values'):
	    def x(y, z=self.results):
		if not z.has_key(y):
		    z[y] = 1
		    return y
	    v = filter(None, map(x, l['values']))
	    if(len(v)):
		reactor.callFromThread(self.callback, v)
	self.schedule()
		
    ## get value
    def schedule(self):
	if self.finished:
	    return
	l = self.found.values()
	l.sort(self.sort)

	for node in l[:K]:
	    if not self.queried.has_key(node.id) and node.id != self.table.node.id:
		#xxx t.timeout = time.time() + GET_VALUE_TIMEOUT
		df = node.findValue(self.target, self.table.node.senderDict())
		df.addCallback(self.handleGotNodes)
		df.addErrback(self.defaultGotNodes)
		self.outstanding = self.outstanding + 1
		self.queried[node.id] = 1
	    if self.outstanding >= N:
		break
	assert(self.outstanding) >=0
	if self.outstanding == 0:
	    ## all done, didn't find it!!
	    self.finished=1
	    reactor.callFromThread(self.callback,[])
    
    ## get value
    def goWithNodes(self, nodes):
	self.results = {}
	for node in nodes:
	    if node.id == self.table.node.id:
		continue
	    self.found[node.id] = node
	    #xxx t.timeout = time.time() + FIND_NODE_TIMEOUT
	    df = node.findValue(self.target, self.table.node.senderDict())
	    df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
	    self.outstanding = self.outstanding + 1
	    self.queried[node.id] = 1
	if self.outstanding == 0:
	    reactor.callFromThread(self.callback, [])


KEINITIAL_DELAY = 60 # 1 minute
KE_DELAY = 60 # 1 minute
KE_AGE = 60 * 5
class KeyExpirer:
    def __init__(self, store, itime, kw):
	self.store = store
	self.itime = itime
	self.kw = kw
	reactor.callLater(KEINITIAL_DELAY, self.doExpire)
	
    def doExpire(self):
	self.cut = `time() - KE_AGE`
	self._expire()
	
    def _expire(self):
	ic = self.itime.cursor()
	sc = self.store.cursor()
	kc = self.kw.cursor()
	irec = None
	try:
	    irec = ic.set_range(self.cut)
	except db.DBNotFoundError:
	    # everything is expired
	    f = ic.prev
	    irec = f()
	else:
	    f = ic.next
	i = 0
	while irec:
	    it, h = irec
	    try:
		k, v, lt = loads(self.store[h])
	    except KeyError:
		ic.delete()
	    else:
		if lt < self.cut:
		    try:
			kc.set_both(k, h)
		    except db.DBNotFoundError:
			print "Database inconsistency!  No key->value entry when a store entry was found!"
		    else:
			kc.delete()
		    self.store.delete(h)
		    ic.delete()
		    i = i + 1
	    irec = f()
	    
	reactor.callLater(KE_DELAY, self.doExpire)
	if(i > 0):
	    print ">>>KE: done expiring %d" % i
	