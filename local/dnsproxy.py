#!/usr/bin/env python
# coding:utf-8
# TODO: 1. improve LRU Cache performance
#       2. sort reply rdata by ip latency
#       3. add tcp query mode


__version__ = '1.0'

import sys
import glob

sys.path += glob.glob('*.egg')

import gevent
import gevent.server
import gevent.timeout
import gevent.monkey
gevent.monkey.patch_all(subprocess=True)

import time
import logging
import collections
import socket
import select
import dnslib

class ExpireDict(object):
    """ A dictionary-like object, supporting expire semantics."""
    def __init__(self, max_size=1024):
        self.max_size = max_size
        self.__values = {}
        self.__expire_times = collections.OrderedDict()

    def size(self):
        return len(self.__values)

    def clear(self):
        self.__values.clear()
        self.__expire_times.clear()

    def exists(self, key):
        return key in self.__values

    def set(self, key, value, expire=0):
        self.__values[key] = value
        if expire:
            self.__expire_times[key] = int(time.time() + expire)
        self.cleanup()

    def get(self, key):
        et = self.__expire_times.get(key, None)
        if et and et < time.time():
            del self.__values[key], self.__expire_times[key]
            self.cleanup()
            raise KeyError(key)
        return self.__values[key]

    def delete(self, key):
        del self.__values[key], self.__expire_times[key]

    def cleanup(self):
        t = int(time.time())
        #Delete expired
        any(self.delete(k) for k, et in self.__expire_times.iteritems() if et < t)
        #If we have more than self.max_size items, delete the oldest
        over_size = len(self.__values) - self.max_size
        if over_size == 1:
            self.delete(next(self.__expire_times.iterkeys()))
        elif over_size > 1:
            any(self.delete(k) for k in [x for i, x in enumerate(self.__expire_times) if i < over_size])


class DNSServer(gevent.server.DatagramServer):
    """DNS TCP Proxy based on gevent/dnslib"""

    def __init__(self, dns_servers, dns_backlist, *args, **kwargs):
        super(self.__class__, self).__init__(*args, **kwargs)
        self.dns_v4_servers = [x for x in dns_servers if ':' not in x]
        self.dns_v6_servers = [x for x in dns_servers if ':' in x]
        self.dns_backlist = set(dns_backlist)
        self.dns_cache = ExpireDict(max_size=4096)

    def handle(self, data, address):
        logging.debug('receive from %r data=%r', address, data)
        request = dnslib.DNSRecord.parse(data)
        qname = str(request.q.qname)
        qtype = request.q.qtype
        try:
            reply_data = self.dns_cache.get((qname, qtype))
        except KeyError:
            reply_data = ''
        sock_v4 = sock_v6 = None
        socks = []
        if self.dns_v4_servers:
            sock_v4 = gevent.socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            socks.append(sock_v4)
        if self.dns_v6_servers:
            sock_v6 = gevent.socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            socks.append(sock_v6)
        reply_ttl = 600
        for _ in xrange(2):
            if reply_data:
                break
            try:
                for dnsserver in self.dns_v4_servers:
                    sock_v4.sendto(data, (dnsserver, 53))
                for dnsserver in self.dns_v6_servers:
                    sock_v6.sendto(data, (dnsserver, 53))
                with gevent.timeout.Timeout(2):
                    while True:
                        if reply_data:
                            break
                        ins, _, _ = select.select(socks, [], [], 0.1)
                        for sock in ins:
                            reply_data, _ = sock.recvfrom(512)
                            reply = dnslib.DNSRecord.parse(reply_data)
                            iplist = [str(x.rdata) for x in reply.rr]
                            if any(x in self.dns_backlist for x in iplist):
                                logging.warning('query qname=%r reply bad iplist=%r', qname, iplist)
                                reply_data = ''
                            else:
                                if reply.rr:
                                    reply_ttl = max(x.ttl for x in reply.rr)
                                logging.info('query qname=%r reply iplist=%s, ttl=%r', qname, iplist, reply_ttl)
                                self.dns_cache.set((qname, qtype), reply_data, reply_ttl * 2)
                                break
            except socket.error as e:
                logging.warning('handle dns data=%r socket: %r', data, e)
            except gevent.timeout.Timeout as e:
                logging.warning('handle dns data=%r timeout: %r', data, e)
        for sock in socks:
            sock.close()
        if reply_data:
            return self.sendto(data[:2] + reply_data[2:], address)


def test():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(asctime)s %(message)s', datefmt='[%b %d %H:%M:%S]')
    dnsservers = ['114.114.114.114']
    backlist = '1.1.1.1|255.255.255.255|74.125.127.102|74.125.155.102|74.125.39.102|74.125.39.113|209.85.229.138|4.36.66.178|8.7.198.45|37.61.54.158|46.82.174.68|59.24.3.173|64.33.88.161|64.33.99.47|64.66.163.251|65.104.202.252|65.160.219.113|66.45.252.237|72.14.205.104|72.14.205.99|78.16.49.15|93.46.8.89|128.121.126.139|159.106.121.75|169.132.13.103|192.67.198.6|202.106.1.2|202.181.7.85|203.161.230.171|203.98.7.65|207.12.88.98|208.56.31.43|209.145.54.50|209.220.30.174|209.36.73.33|209.85.229.138|211.94.66.147|213.169.251.35|216.221.188.182|216.234.179.13|243.185.187.3|243.185.187.39'.split('|')
    logging.info('serving at port 53...')
    DNSServer(dnsservers, backlist, ('', 53)).serve_forever()


if __name__ == '__main__':
    test()
