# -*- coding: utf-8 -*-

_qmark = '?'
try:
    import oursql as MySQLdb
except:
    import MySQLdb
    _qmark = '%s'

import pywikibot
from pywikibot import config
import dbsettings

class ReportLogger(object):
    """
    Base class for report logger
    """
    def __init__(self, site=None):
        pass

    def add_report(self, diff, page_title, page_ns, ithenticate_id, report):
        pass

class DbReportLogger(ReportLogger):
    """
    Db report logger logs reports to database
    """
    def __init__(self, site=None):
        self.conn = None
        self.cursor = None
        site = pywikibot.Site() if site is None else site
        self.project = site.family.name
        self.lang = site.code

    def connect(self):
        self.conn = MySQLdb.connect(host=dbsettings.reporter_db_host,
                           db='{}__copyright'.format(config.db_username),
                           read_default_file=config.db_connect_file)
        self.cursor = self.conn.cursor()
 
    def add_report(self, diff, page_title, page_ns, ithenticate_id, report):
        global _qmark
        if self.conn is None:
            self.connect()

        try:
            print((self.project, self.lang, diff, page_title, page_ns, ithenticate_id, report))
            insert_query = """INSERT INTO copyright_diffs (project, lang, diff, page_title, page_ns, ithenticate_id, report)
            values ({}, {}, {}, {}, {}, {}, {} )
            """.format(*[_qmark]*7)
            self.cursor.execute(insert_query, (self.project, self.lang, diff, page_title, int(page_ns), ithenticate_id, report))
            self.conn.commit()
        except MySQLdb.OperationalError:
            self.connect()

