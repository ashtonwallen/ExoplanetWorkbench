"""Durable allowance for unique input FITS bytes, including cached products."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

current = ContextVar('download_allowance',default=None)


class InputAllowance:
    def __init__(self,path,maximum):
        self.path,self.maximum = path,maximum
        self.files = json.loads(path.read_text()) if path.exists() else {}

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.files))
        tmp.replace(self.path)

    def reserve(self,uri,hint,request_max):
        remaining = self.maximum-sum(v for k,v in self.files.items() if k!=uri)
        allowed = min(request_max,remaining)
        amount = int(hint) if hint and int(hint)>0 else allowed
        if allowed<=0 or amount>allowed:
            raise ValueError('Automated research input-data allowance exhausted')
        self.files[uri] = amount
        self.save()
        return min(allowed,amount)

    def settle(self,uri,size):
        if size>self.files[uri]:
            raise ValueError('Product exceeded its reserved input allowance')
        self.files[uri]=size
        self.save()

    @contextmanager
    def activate(self):
        token=current.set(self)
        try:
            yield self
        finally:
            current.reset(token)
