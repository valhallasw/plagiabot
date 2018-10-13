# -*- coding: utf-8 -*-
from pywikibot.data.api import APIError
import pywikibot
from pywikibot import config
import dbsettings

_qmark = '?'
try:
    import oursql as MySQLdb
except:
    import MySQLdb

    _qmark = '%s'


class ReportLogger(object):
    """
    Base class for report logger
    """

    def __init__(self, site=None):
        self.site = site
        self._page_triage = False
        pass

    def add_report(self, diff, diff_ts, page_title, page_ns, ithenticate_id, report):
        if self.page_triage:
            self.page_triage_copyvio(diff)

    def page_triage_copyvio(self, diff):
        token = self.site.tokens['csrf']
        params = {
            'action': 'pagetriagetagcopyvio',
            'token': token,
            'revid': diff
        }
        request = self.site._request(parameters=params, use_get=False)
        try:
            response = request.submit()
        except APIError as e:
            # silently drop it
            pywikibot.output('Triage triage {}: {}'.format(diff, str(e)))

    @property
    def page_triage(self):
        return self._page_triage

    @page_triage.setter
    def page_triage(self, val):
        if not self.site:
            self.site = pywikibot.Site()
        if val and not self.site.has_group('copyviobot'):
            raise Exception('Invalid user rights. user must belong to copyviobot group')
        self._page_triage = val


class DbReportLogger(ReportLogger):
    """
    Db report logger logs reports to database
    """

    def __init__(self, site=None):
        super(DbReportLogger, self).__init__(site)
        self.conn = None
        self.cursor = None
        self.site = pywikibot.Site() if site is None else site
        self.project = site.family.name
        self.lang = site.code

    def connect(self):
        self.conn = MySQLdb.connect(host=dbsettings.reporter_db_host,
                                    db='{}__copyright_p'.format(config.db_username),
                                    read_default_file=config.db_connect_file)
        self.cursor = self.conn.cursor()

    def add_report(self, diff, diff_ts, page_title, page_ns, ithenticate_id, report):
        global _qmark
        super(DbReportLogger, self).add_report(diff, diff_ts, page_title, page_ns, ithenticate_id, report)
        if self.conn is None:
            self.connect()  # TODO: handle InterfaceError: Can't connect to MySQL server...?
        diff_ts = diff_ts.totimestampformat()  # use MW format
        retries = 0
        while retries < 2:
            try:
                insert_query = """INSERT INTO copyright_diffs (project, lang, diff, diff_timestamp, page_title, page_ns, ithenticate_id, report)
                values ({}, {}, {}, {}, {}, {}, {}, {} )
                """.format(*[_qmark] * 8)
                self.cursor.execute(insert_query, (
                    self.project, self.lang, diff, diff_ts, page_title.replace(' ', '_'), int(page_ns), ithenticate_id,
                    report))
                self.conn.commit()
                break
            except MySQLdb.OperationalError:
                self.connect()
                retries += 1
            except MySQLdb.IntegrityError:
                break
