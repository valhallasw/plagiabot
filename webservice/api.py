from plagiabot_config import ithenticate_user, ithenticate_password
from flup.server.fcgi import WSGIServer

from cgi import parse_qs, escape

class csv_formatter(object):
    def __init__(self):
        self.headers = None
    def __call__(self, row):
        res = ''
        if self.headers is None:
            self.headers = [str(x) for x in row]
            res = ','.join(self.headers)
        res += '\n' + ','.join([str(row[h]) if h in row else '' for h in self.headers])
        return res

    def open(self):
        return [('Content-Type', 'text/plain; charset=UTF-8')]

    def close(self):
        return ''

class json_formatter(object):
    def __init__(self):
        self.is_first = True

    def __call__(self, row):
        res = ',\n'
        if self.is_first:
            res  = '['
            self.is_first = False
        return res+str(row)

    def open(self):
        return [('Content-Type', 'text/javascript; charset=UTF-8')]

    def close(self):
        return '[]' if self.is_first else ']'

def suspected_diffs(q):
    import oursql 
    import dbsettings
    con = oursql.connect(host=dbsettings.reporter_db_host, db='{}__copyright_p'.format(dbsettings.db_username),
                          read_default_file=dbsettings.connect_file, use_unicode=True, charset='utf8')
    cursor = con.cursor()
    columns = ['project','lang','diff', 'diff_timestamp','page_title','page_ns']

    where_cols = []
    value_cols = []
    for col in columns:
        if col in q:
            where_cols.append(col)
            value_cols.append(q[col][0])

    where = ''
    if len(where_cols):
        where = ' where ' + ' AND '.join([x+'= ?' for x in where_cols])
    cursor.execute('select project, lang, diff, diff_timestamp, page_title, page_ns from copyright_diffs' + where + ' order by diff_timestamp desc limit 50', value_cols)
    for data in cursor:
        yield dict((col, str(data[i])) for i, col in enumerate(columns) if col not in where_cols)

def get_view_url(q):
    if 'report_id' not in q:
        return 'Missing report_id'
    report_id = q['report_id'][0]
    import xmlrpclib
    try:
        server_i = xmlrpclib.ServerProxy("https://api.ithenticate.com/rpc")
        login_response = server_i.login({"username": ithenticate_user, "password": ithenticate_password})
        assert (login_response['status'] == 200)
        sid = login_response['sid']
        report = server_i.report.get({'id': report_id, 'sid': sid})
        return report['view_only_url']
    except xmlrpclib.ProtocolError as e:
       return ';-('#+'!'+e.__class__.__name__+e.errmsg+'!'+str(e.errcode)+'%s'%e.headers
    except Exception as e:
       return ';('

def app(environ, start_response):
    q = parse_qs(environ['QUERY_STRING'])
    formatter = json_formatter()
    if 'format' in q:
        if q['format'][0] == 'json':
            formatter = json_formatter()
        elif q['format'][0] == 'csv':
            formatter = csv_formatter()
        else:
            start_response('200 OK', [('Content-Type', 'text/plain; charset=UTF-8')])
            yield 'Unkown format %s' %(str(q['format']))
            return
    start_response('200 OK', formatter.open())
    valid_actions = ['suspected_diffs', 'get_view_url']
    if 'action' not in q or q['action'][0] not in valid_actions:
        yield 'Invalid action.\nMust be one of the following: ' + ', '.join(valid_actions)
        return
    action = q['action'][0]
    if action =='suspected_diffs':
        for diff in suspected_diffs(q):
            yield formatter(diff)
    elif action == 'get_view_url':
        yield formatter(get_view_url(q))

    yield formatter.close()

WSGIServer(app).run()

