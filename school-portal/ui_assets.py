"""Content versions for cacheable public UI files; account responses stay private."""
import hashlib
from functools import lru_cache
from pathlib import Path
from flask import request
from flask.sessions import SecureCookieSessionInterface


@lru_cache(maxsize=256)
def _digest(path,modified,size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class PublicAssetSessionInterface(SecureCookieSessionInterface):
    def save_session(self,app,session,response):
        # Static UI files are public and must not refresh or emit account cookies.
        if request.endpoint=='static':return
        return super().save_session(app,session,response)


def install(app):
    app.session_interface=PublicAssetSessionInterface()
    root=Path(app.static_folder).resolve()
    def version(filename):
        try:
            path=(root/filename).resolve()
            if not path.is_relative_to(root) or not path.is_file():return None
            info=path.stat()
            return _digest(str(path),info.st_mtime_ns,info.st_size)
        except (OSError,TypeError,ValueError):return None
    @app.url_defaults
    def versioned_static(endpoint,values):
        if endpoint=='static' and (value:=version(values.get('filename',''))):values['v']=value
    app.extensions['ui_assets']=dict(version=version)
