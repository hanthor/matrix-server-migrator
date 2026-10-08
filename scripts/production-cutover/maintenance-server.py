#!/usr/bin/env python3
"""Stateless maintenance HTTP response. Never logs URLs, headers or bodies."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

ERROR=json.dumps({'errcode':'M_UNKNOWN','error':'Homeserver maintenance in progress. Please retry later.'},separators=(',',':')).encode()
PUBLIC_HOSTS={h.lower().rstrip('.') for h in os.environ.get('PUBLIC_HOSTS','').split(',') if h}

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):
        pass
    def log_error(self,*args):
        pass
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def handle_expect_100(self):
        self.respond()
        return False
    def __getattr__(self,name):
        if name.startswith('do_'):
            return self.respond
        raise AttributeError(name)
    def respond(self):
        self.close_connection=True
        # Ready is for direct backend probes. Public Matrix/MAS hosts never
        # report healthy for a user API request, even /ready on MAS's '/' rule.
        host=(urlsplit('//'+self.headers.get('Host','')).hostname or '').lower().rstrip('.')
        internal_ready=self.path.split('?',1)[0]=='/ready' and host not in PUBLIC_HOSTS
        if self.command=='OPTIONS':
            status,body=204,b''
        elif internal_ready and self.command in ('GET','HEAD'):
            status,body=200,b'{"ready":true}'
        else:
            status,body=503,ERROR
        self.send_response_only(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Retry-After','60')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type, X-Requested-With, If-Match, If-None-Match')
        self.send_header('Access-Control-Expose-Headers','Retry-After')
        self.send_header('Connection','close')
        self.end_headers()
        if self.command!='HEAD' and body:
            self.wfile.write(body)

class QuietServer(HTTPServer):
    request_queue_size=128
    def handle_error(self,*args):
        pass

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind',default='0.0.0.0');parser.add_argument('--port',type=int,default=8008)
    args=parser.parse_args();QuietServer((args.bind,args.port),Handler).serve_forever()
