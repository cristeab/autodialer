#!/usr/bin/env python

import os
import thread
import threading
import time

import __builtin__
import eventlet


class HubCache(dict):
    """Cache used by Notifier instances

    This is a dict-subclass to overwrite the .clear() method.  It's
    keys are hubs and values are (rfd, wfd, listener).  Using this
    means you can clear the cache in a way which will unregister the
    listeners from the hubs and close all filedescriptors.

    XXX This is hugely incomplete, only remove items from this cache
        using the .clear() method as the other ways of removing items
        will not release resources properly.
    """

    def clear(self):
        while self:
            hub, (rfd, wfd, listener) = self.popitem()
            hub.remove(listener)
            os.close(rfd)
            os.close(wfd)

    def __del__(self):
        self.clear()


"""The global hubcache

This is the default hubcache used by Notifier instances.
"""
GLOBAL_HUBCACHE = HubCache()


class Notifier(object):
    """Notify one or more waiters

    This is essentially a condition without the lock.  It can be used
    to signal between threads and greenlets at will.
    """

    # This doesn't use eventlet.hubs.trampoline since that results in
    # a filedescriptor per waiting greenlet.  Instead each eventlet
    # that calls .gwait() will ensure there's a filedescriptor
    # registered for reading for with it's hub.  This filedescriptor
    # is then only used when another thread wants to wake up the hub
    # in order for a notification to be delivered to the eventlet.

    def __init__(self, hubcache=GLOBAL_HUBCACHE):
        """Initialise the notifier

        The hubcache is a dictionary which will keep pipes used by the
        notifier so that only ever one pipe gets created per hub.  The
        default is to share this hubcache globally so all notifiers
        use the same pipes for intra-hub communication.
        """
        # Each item in this set is a tuple of (waiter, hub).  For an
        # eventlet the waiter is the greenlet while for a thread it is
        # a lock.  For a thread the hub item is always None.
        self._waiters = set()
        self.hubcache = hubcache

    def wait(self, timeout=None):
        """Wait from a thread or eventlet

        This blocks the current thread/eventlet until it gets woken up
        by a call to .notify() or .notify_all().

        This will automatically dispatch to .gwait() or .twait() as
        needed so that the blocking will be cooperative for greenlets.

        Returns True if this thread/eventlet was notified and False
        when a timeout occurred.
        """
        hub = eventlet.hubs.get_hub()
        if hub.running:
            self.gwait(timeout)
        else:
            self.twait(timeout)

    def gwait(self, timeout=None):
        """Wait from an eventlet

        This cooperatively blocks the current eventlet by switching to
        the hub.  The hub will switch back to this eventlet when it
        gets notified.

        Usually you can just call .wait() which will dispatch to this
        method if you are in an eventlet.

        Returns True if this thread/eventlet was notified and False
        when a timeout occurred.
        """
        waiter = eventlet.getcurrent()
        hub = eventlet.hubs.get_hub()
        self._create_pipe(hub)
        self._waiters.add((waiter, hub))
        if timeout and timeout > 0:
            timeout = eventlet.Timeout(timeout)
            try:
                with timeout:
                    hub.switch()
            except eventlet.Timeout, t:
                if t is not timeout:
                    raise
                self._waiters.discard((waiter, hub))
                return False
            else:
                return True
        else:
            hub.switch()
            return True

    def twait(self, timeout=None):
        """Wait from an thread

        This blocks the current thread by using a conventional lock.

        Usually you can just call .wait() which will dispatch to this
        method if you are in an eventlet.

        Returns True if this thread/eventlet was notified and False
        when a timeout occurred.
        """
        waiter = threading.Lock()
        waiter.acquire()
        self._waiters.add((waiter, None))
        if timeout is None:
            waiter.acquire()
            return True
        else:
            # Spin around a little, just like the stdlib does
            _time = time.time
            _sleep = time.sleep
            min = __builtin__.min
            endtime = _time() + timeout
            delay = 0.0005  # 500 us -> initial delay of 1 ms
            while True:
                gotit = waiter.acquire(0)
                if gotit:
                    break
                remaining = endtime - _time()
                if remaining <= 0:
                    break
                delay = min(delay * 2, remaining, .05)
                _sleep(delay)
            if not gotit:
                self._waiters.discard((waiter, None))
                return False
            else:
                return True

    def notify(self):
        """Notify one waiter

        This will notify one waiter, regardless of whether it is a
        thread or eventlet, resulting in the waiter returning from
        it's .wait() call.

        This will never block itself so can be called from either a
        thread or eventlet itself and will wake up the hub of another
        thread if an eventlet from it is notified.
        """
        if self._waiters:
            waiter, hub = self._waiters.pop()
            if hub is None:
                # This is a waiting thread
                try:
                    waiter.release()
                except thread.error:
                    pass
            else:
                # This is a waiting greenlet
                def notif(waiter):
                    waiter.switch()

                hub.schedule_call_global(0, notif, waiter)
                if hub is not eventlet.hubs.get_hub():
                    self._kick_hub(hub)

    def notify_all(self):
        """Notify all waiters

        Similar to .notify() but will notify all waiters instead of
        just one.
        """
        for i in xrange(len(self._waiters)):
            self.notify()

    def _create_pipe(self, hub):
        """Create a pipe for a hub

        This creates a pipe (read and write fd) and registers it with
        the hub so that ._kick_hub() can use this to signal the hub.

        This keeps a cache of hubs on ``self.hubcache`` so that only
        one pipe is created per hub.  Furthermore this dict is never
        cleared implicitly to avoid creating new sockets all the time.

        This method is always called from .gwait() and therefore can
        only run once for a given hub at the same time.  Thus it is
        threadsave.
        """
        if hub in self.hubcache:
            return

        def read_callback(fd):
            # This just reads the (bogus) data just written to empty
            # the os queues.  The only purpose was to kick the hub
            # round it's loop which is now has.  The notif function
            # scheduled by .notify() will now do it's work.
            os.read(fd, 512)

        rfd, wfd = os.pipe()
        listener = hub.add(eventlet.hubs.hub.READ, rfd, read_callback)
        self.hubcache[hub] = (rfd, wfd, listener)

    def _kick_hub(self, hub):
        """Kick the hub around it's loop

        Threads need to be able to kick a hub around their loop by
        interrupting the sleep.  This is done with the help of a
        filedescriptor to which the thread writes a byte (using this
        method) which will then wake up the hub.
        """
        rfd, wfd, r_listener = self.hubcache[hub]
        current_hub = eventlet.hubs.get_hub()
        if current_hub.running:
            def write(fd):
                os.write(fd, 'A')
                current_hub.remove(w_listener)

            w_listener = current_hub.add(eventlet.hubs.hub.WRITE, wfd, write)
        else:
            os.write(wfd, 'A')

    def __repr__(self):
        return ('<gsync.Notifier object at 0x%x (%d waiters)>' %
                (id(self), len(self._waiters)))
