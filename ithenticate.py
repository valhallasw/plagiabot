import xmlrpclib
import sys
sys.path.append('../plagiabot')
from plagiabot_config import ithenticate_user, ithenticate_password
from flup.server.fcgi import WSGIServer
from cgi import parse_qs, escape

#cgitb.enable()
def get_view_url(report_id):
    report='a'
    a=''
    try:
        a='a'
        server_i = xmlrpclib.ServerProxy("https://api.ithenticate.com/rpc")
        a='b'
        login_response = server_i.login({"username": ithenticate_user, "password": ithenticate_password})
        a='log'
        assert (login_response['status'] == 200)
        a='loged'
        sid = login_response['sid']
        a='log1'
        report = server_i.report.get({'id': report_id, 'sid': sid})
        a='log2'
        return report['view_only_url']
        #return report
    except xmlrpclib.ProtocolError as e:
       return ';-('#+'!'+e.__class__.__name__+e.errmsg+'!'+str(e.errcode)+'%s'%e.headers
    except Exception as e:
       return ';('


def app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/html')])
    q=parse_qs(environ['QUERY_STRING'])
    yield '<h1>Redirecting to similarity report...</h1>'
    if 'rid' in q and len(q['rid']) > 0:
        url=get_view_url(q['rid'][0])
        if 'http' in url:
            #yield '<br>To get to report please follow <a href="%s">%s</a>.'%(url, url)
            yield '<script>window.location.href="%s";</script>'%url
        else:
            yield url

WSGIServer(app).run()

