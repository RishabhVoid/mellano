"""Bounded public HTTP range reader for GDAL's Python opener interface."""
import io
from collections import OrderedDict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HTTPRangeReader(io.RawIOBase):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.position = 0
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504])))
        response = self.session.head(url, timeout=(20,40))
        response.raise_for_status()
        self.length = int(response.headers["Content-Length"])
        self.block_size = 1024 * 1024
        self.blocks = OrderedDict()
        self.mode = "rb"

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        if self.position < 0: raise ValueError("Negative position")
        return self.position

    def read(self, size=-1):
        size = self.length-self.position if size < 0 else min(size,self.length-self.position)
        if size <= 0: return b""
        chunks = []
        while size:
            index, offset = divmod(self.position, self.block_size)
            if index not in self.blocks:
                start = index*self.block_size
                end = min(self.length-1,start+self.block_size-1)
                response = self.session.get(self.url, headers={"Range": f"bytes={start}-{end}"}, timeout=(20,90))
                response.raise_for_status()
                if response.status_code != 206 or len(response.content) != end-start+1:
                    raise IOError("Server did not honor the bounded range request")
                self.blocks[index] = response.content
                if len(self.blocks)>128: self.blocks.popitem(last=False)
            block = self.blocks[index]
            count = min(size,len(block)-offset)
            chunks.append(block[offset:offset+count])
            self.position += count
            size -= count
        return b"".join(chunks)

    def close(self):
        self.session.close()
        self.blocks.clear()
        super().close()


def range_opener(url):
    name = url.rsplit("/",1)[-1]
    def opener(path, mode="rb"):
        if str(path).rsplit("/",1)[-1] != name or mode not in ("r","rb"):
            raise FileNotFoundError(str(path))
        return HTTPRangeReader(url)
    return opener
