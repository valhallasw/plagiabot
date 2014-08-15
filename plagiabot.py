#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Script to catch copyright violations.
The script use DB or API to get diffs, and sends inserts and replacements to an external service 
to find copyright violations.

Output can be to console (default) or to wiki page 

Command line options:
    -report:Page 		page name to write report to.
    -talkTempalte:XX	Run on diffs of a pages with talk page containing {{talkTemplate}}
    -recentchanges:X	Number of days to fetch recent changes. For 12 hours set 0.5.

Usage examples:

Report on possible violations in Wikiproject Medicine related articles:
    python plagtarismbot.py -lang:en -report:"Wikipedia:MED/Copyright" -talkTemplate:"WikiProject_Medicine"

Report on possible violations in the last 3 days to console:
    python plagtarismbot.py -recentchanges:3

Report on possible violations in the top 100 recent changes (no DB access required):
    python plagtarismbot.py -api_recentchanges:100
"""
import time
import datetime
import difflib
import pywikibot
import MySQLdb
import re
import dbsettings
import uuid
import xmlrpclib
from plagiabot_config import ithenticate_user, ithenticate_password

MIN_SIZE = 500  # minimum length of added text for sending to server
MIN_PARCENTAGE = 50

messages = {
    'en': {
        'table-title': 'Title',
        'table-editor': 'Editor',
        'table-diff': 'Diff',
        'template-diff': u'Diff',
        'table-source': 'Source',
        'update-summary': 'Update',
        'ignore_summary': 'Reverted edits by%%'
    },
    'he': {
        'table-title': u'כותרת',
        'table-editor': u'עורך/עורכת',
        'table-diff': u'הבדל',
        'template-diff': u'הבדל',
        'table-source': u'מקורות',
        'update-summary': u'עדכון',
        'ignore_summary': u'שוחזר מעריכות של%%'
    }
}


class PlagiaBot:
    def __init__(self, site, generator, report_page=None):
        self.generator = generator

        # variables for connecting to server
        self.server = None
        self.folder = None
        self.sid = None
        self.site = site
        self.report_page = None if report_page is None else pywikibot.Page(self.site, report_page)

    def _init_server(self):
        self.server = xmlrpclib.ServerProxy("https://api.ithenticate.com/rpc")

        pywikibot.output("Logging in to ithenticate...")
        login_response = self.server.login({"username": ithenticate_user, "password": ithenticate_password})
        assert (login_response['status'] == 200)
        self.sid = login_response['sid']

        pywikibot.output("Finding folder to upload into, with name 'Wikipedia'...")
        folder_list_response = self.server.folder.list({'sid': self.sid})
        assert (folder_list_response['status'] == 200)

        for folder in folder_list_response['folders']:
            if folder['name'] == 'Wikipedia':
                self.folder = folder
                break

        if self.folder is None:
            raise Exception('No Wikipedia folder found!')
        else:
            pywikibot.output("\tFound")


    def upload_diff(self, plagiatext, title, diffId):
        if self.server is None:
            self._init_server()
        pywikibot.output("Upload text to server...")

        SUBMIT_TO_GENERATE_REPORT = 1
        SUBMIT_TO_STORE_IN_REPOSITORY = 2
        SUBMIT_TO_STORE_IN_REPOSITORY_AND_GENERATE_REPORT = 3

        submit_response = self.server.document.add({
            'sid': self.sid,
            'submit_to': SUBMIT_TO_GENERATE_REPORT,
            'folder': self.folder['id'],
            'uploads': [{'title': '%s - %i' % (title, uuid.uuid4()),
                         'author_first': 'Random',
                         'author_last': 'Author',
                         'filename': diffId,
                         'upload': xmlrpclib.Binary(plagiatext)}]
        })
        if submit_response['status'] != 200:
            pywikibot.output(submit_reponse)
            raise Exception("Invalid status from server")
        try:
            upload = submit_response['uploaded'][0]
            return upload['id']
        except:
            print(submit_response)
            raise

    def poll_response(self, upload_id):
        global MIN_PARCENTAGE
        pywikibot.output("Polling iThenticate until document has been processed...", newline=False)

        while True:
            document_get_response = self.server.document.get({'id': upload_id, 'sid': self.sid})
            assert (document_get_response['status'] == 200)
            document = document_get_response['documents'][0]
            pending = document['is_pending']
            if not pending:
                break
            pywikibot.output('.', newline=False)
            time.sleep(5)
        pywikibot.output('.')

        for part in document['parts']:
            # not sure if there is always a single part, so looping over it instead.
            pywikibot.output("Part #%i has a %i%% match. Getting details..." % (part['id'], part['score']))
            report_get_response = self.server.report.get({'id': part['id'], 'sid': self.sid})
            assert (report_get_response['status'] == 200)
            pywikibot.output("Details are available on %s" % (report_get_response['report_url']))
            report_sources_response = self.server.report.sources({'id': part['id'], 'sid': self.sid})
            assert (report_sources_response['status'] == 200)

            pywikibot.output("Sources found were:")
            report = []
            sources = [cp_source for cp_source in report_sources_response['sources'] if
                       '.wikipedia.org' not in cp_source['linkurl']]
            for source in sources[:2]:
                if int(source['percent']) > MIN_PARCENTAGE:
                    report.append("* %s % 3i%% %i words at %s" % (
                        source['collection'][0], source['percent'], source['word_count'], source['linkurl']))
            report = '\n'.join(report)
            return report

    def run(self):
        global MIN_SIZE
        if self.report_page is None:
            orig_report = [""]
        else:
            orig_report = self.report_page.get()
            orig_report = orig_report.split('==', 1)
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']
        reports = u"""
== ~~~~~ ==
{| class="wikitable sortable"
! %s !! %s !! %s !! %s
""" % (local_messages['table-title'], local_messages['table-diff'], local_messages['table-editor'],
       local_messages['table-source'])
        uploads = []
        for p, new_rev, prev_rev in self.generator:
            pywikibot.output('Title: %s' % p.title())
            pywikibot.output('\tPrev: %i\tNew:%i' % (prev_rev, new_rev))
            try:
                self.site.loadrevisions(p,
                                        getText=True,
                                        revids=[new_rev, prev_rev])
                old = "" if prev_rev == 0 else p.getOldVersion(prev_rev).splitlines(1)
                new = p.getOldVersion(new_rev).splitlines(1)
                editor = p._revisions[new_rev].user  # TODO: is there a non private access to user in revisions?
            except:
                pywikibot.output("Error occurred - skipping")
                continue

            diffy = difflib.SequenceMatcher()
            diffy.set_seqs(old, new)
            diff = [''.join(new[after_start:after_end]) for opcode, before_start, before_end, after_start, after_end in
                    diffy.get_opcodes() if opcode in ['insert', 'replace']]
            # other option:
            # diff = ''.join(difflib.unified_diff(old, new))
            # diff = [line[1:] for line in diff.splitlines() if re.match('\+[^\+]', line)]

            # clean some html/wikitext from the text before sending to server...
            # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
            added_lines = pywikibot.removeHTMLParts(u'\n'.join(diff), keeptags=[])
            added_lines = re.sub("\[\[[^\[\]]+\|([^\[\]]+)\]\]", "\\1", added_lines)  # [[link|textlink]]
            added_lines = re.sub("\[\[(.+?)\]\]", "\\1", added_lines)  # [[links]]
            added_lines = re.sub("\{\{.*?\}\}", "", added_lines)  # templates
            added_lines = re.sub("\[https?:.*?\]", "", added_lines)  # external links
            if len(added_lines) > MIN_SIZE:
                pywikibot.output('Uploading to server')
                pywikibot.output('-------------------')
                try:
                    upload_id = self.upload_diff(added_lines.encode('utf8'), p.title(), "/%i" % new_rev)
                    uploads.append((p.title(), editor, new_rev, prev_rev, upload_id))
                except:
                    print('Skipping - due to error')
                    continue
            else:
                pywikibot.output('Change is too small - skipping')
                # pywikibot.output(added_lines)
                #pywikibot.output('^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')

        pywikibot.output('Polling uploads')
        for title, editor, new_rev, prev_rev, upload_id in uploads:
            report = self.poll_response(upload_id)
            if len(report) > 0:
                reports += """
|- valign="top"
| [[%s]] 
| {{%s|%s|%i|%i}}
| [[User:%s|]]
|
%s""" % (title, local_messages['template-diff'], title, new_rev, prev_rev, editor, report)
                # break
            else:
                pywikibot.output('skip')
        reports += "\n|}"
        if len(orig_report) == 2:
            reports = orig_report[0] + reports + "\n==" + orig_report[1]
        else:
            reports = orig_report[0] + reports
        if len(uploads) > 0:
            if self.report_page is None:
                pywikibot.output(reports)
            else:
                self.report_page.put(reports, "Update")
        else:
            pywikibot.output('No violation found!')


def db_changes_generator(site, talkTemplate=None, days=0.25):
    """
    Generator for changes in specific wikiproject
    """
    pywikibot.output('Connecting to %s' % (dbsettings.host % site.dbName()))
    conn = MySQLdb.connect(host=dbsettings.host % site.dbName(),
                           db=dbsettings.dbname % site.dbName(),
                           read_default_file=dbsettings.connect_file)
    dateLimit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d%H%M%S')
    cursor = conn.cursor()
    if talkTemplate is None:
        talk_sql = ""
    else:
        talk_sql = """
 inner join
    (
    select page_title as talk_title
    from
        templatelinks
    inner join
                page 
        on 
                page_id=tl_from and 
                page_namespace=1 
        where 
                tl_title='%s' and
                tl_namespace=10 and tl_from_namespace=1)
                talkpagemed 
        on 
                rc_title=talk_title
""" % talkTemplate
    ignore_summary = messages[site.lang]['ignore_summary'] if site.lang in messages else ''

    cursor.execute('''/* copyright */
select rc_this_oldid, rc_last_oldid, rc_title, rc_new_len-rc_old_len as diffSize
from
    recentchanges
%s
    left join
        user_groups
    on
        rc_user=ug_user and
        rc_type < 5 and
        ug_group = 'bot'
    where ug_group is NULL and
        rc_namespace=0 and
        rc_timestamp > %s and
        rc_new_len-rc_old_len>500 and
        rc_comment not like '%s'
    order by  rc_new_len-rc_old_len desc
''' % (talk_sql, dateLimit, ignore_summary))
    changes = []
    for curid, prev_id, title, diffSize in cursor.fetchall():
        changes.append((pywikibot.Page(site, title.decode('utf-8')), curid, prev_id))
    pywikibot.output('Num changes: %i' % len(changes))
    return changes


def main(*args):
    """
    Handle arguments using standard pywikibot args handling and then runs the bot main functionality.

    """
    report_page = None

    report_page = None
    generator = None
    for arg in pywikibot.handleArgs(*args):
        site = pywikibot.getSite()
        if arg.startswith('-talkTemplate:'):
            generator = db_changes_generator(site, talkTemplate=arg[len("-talkTemplate:"):])
        elif arg.startswith('-recentchanges:'):
            generator = db_changes_generator(site, days=float(arg[len("-recentchanges:"):]))
        elif arg.startswith('-api_recentcahnges:'):
            source = RecentChangesPageGenerator(namespaces=[0], showBot=False,
                                                   total=int(arg[len("-api_recentchanges:"):]), changetype=['edit'],
                                                   showRedirects=False)
            generator = [(p, p.latestRevision(), p.previousRevision()) for p in source]
        elif arg.startswith('-report:'):
            report_page = arg[len("-report:"):]

    if generator is None:
        pywikibot.showHelp()
    else:
        bot = PlagiaBot(pywikibot.getSite(), generator, report_page)
        bot.run()


if __name__ == "__main__":
    try:
        main()
    except:
        import traceback
        traceback.print_exc()
        pywikibot.stopme()
