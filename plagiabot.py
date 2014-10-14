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
import requests
from plagiabot_config import ithenticate_user, ithenticate_password
import urllib

MIN_SIZE = 500  # minimum length of added text for sending to server
MIN_PERCENTAGE = 50
DIFF_URL = '//tools.wmflabs.org/eranbot/ithenticate.py?rid=%s'
messages = {
    'en': {
        'table-title': 'Title',
        'table-editor': 'Editor',
        'table-diff': 'Diff',
        'table-status': 'Status',
        'template-diff': u'Diff',
        'table-source': 'Source',
        'update-summary': 'Update',
        'ignore_summary': '\[*(Reverted|Undid revision|rv$)'
    },
    'he': {
        'table-title': u'כותרת',
        'table-editor': u'עורך/עורכת',
        'table-diff': u'הבדל',
        'table-status': u'מצב',
        'template-diff': u'הבדל',
        'table-source': u'מקורות',
        'update-summary': u'עדכון',
        'ignore_summary': u'(שוחזר מעריכות של|ביטול גרסה|שחזור עריכות)'
    }
}
DEBUG_MODE = False
ignore_sites = [re.compile('\.wikipedia\.org'), re.compile('he-free.info'),
                re.compile('lrd.yahooapis.com')]

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

    def upload_diff(self, plagiatext, title, diff_id):
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
                         'filename': diff_id,
                         'upload': xmlrpclib.Binary(plagiatext)}]
        })
        if submit_response['status'] != 200:
            pywikibot.output(submit_response)
            raise Exception("Invalid status from server")
        try:
            upload = submit_response['uploaded'][0]
            return upload['id']
        except:
            print(submit_response)
            raise

    def poll_response(self, upload_id, article_title, added_lines):
        global MIN_PERCENTAGE, DIFF_URL
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
                       'linkurl' in cp_source and not any([ig.search(cp_source['linkurl']) for ig in ignore_sites])]
            num_sources = 0
            for source in sources:
                if int(source['percent']) > MIN_PERCENTAGE:
                    hint_text = ''
                    try:
                        if source['linkurl'] in added_lines:  # the source is mentioned in the added text
                            hint_text = '<span class="success">citation</span>'
                        else:
                            req_source = requests.get(source['linkurl'])
                            if req_source.status_code == 200:
                                title_encode = urllib.quote(article_title)
                                mirror_re = re.compile('(wikipedia.org/w(iki/|/index.php\?title=)(%s|%s)|material from the Wikipedia article|From Wikipedia|source: wikipedia.org)' % (
                                    re.sub('[ _]', '[ _]', re.escape(article_title)), title_encode), re.I)
                                if any(mirror_re.findall(req_source.text)):
                                    hint_text = '<span class="success">Mirror?</span>'
                                elif any(
                                        re.findall('under (the terms of the Creative Commons Attribution License)|<a href="http://creativecommons.org/licenses/',
                                                   req_source.text, re.I)):
                                    if any(re.findall('<a href="http://creativecommons.org/licenses/(.+?)/', req_source.text, re.I)):
                                        cc_type = re.search('<a href="http://creativecommons.org/licenses/(.+?)/', req_source.text, re.I)
                                        hint_text = '<span class="success">(CC-'+cc_type.group(1)+')</span>'
                                    else:
                                        hint_text = '<span class="success">(CC) (is it NC?)</span>'
                                elif any(re.findall('domain is for sale|buy this domain|get your domain name', req_source.text, re.I)) or \
                                        (re.search('<html', req_source.text, re.I) and
                                            len(re.findall('<a [^>]*>', req_source.text, re.I)) < 10):
                                    hint_text = '<span class="error">Low quality site</span>'
                                    continue  #  low quality sites
                            elif req_source.status_code in [403,404, 500]:
                                continue  # low quality source - ignore
                        num_sources += 1
                    except requests.exceptions.ConnectionError:
                        hint_text = '<span class="error">connection error</span>'
                        continue  # we trust it enough by now to just skip those results
                    except:
                        num_sources += 1
                        pass
                    report.append("* %s % 3i%% %i words at %s %s" % (
                        source['collection'][0], source['percent'], source['word_count'], source['linkurl'], hint_text))
                    if num_sources == 3:
                        break
            report = '[%s report]\n'%DIFF_URL%part['id']+'\n'.join(report) if len(report)>0 else ''
            return report

    def remove_wikitext(self, text):
        # clean some html/wikitext from the text before sending to server...
        # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
        clean_text = pywikibot.removeHTMLParts(text, keeptags=[])
        clean_text = re.sub("\[\[[^\[\]]+\|([^\[\]]+)\]\]", "\\1", clean_text)  # [[link|textlink]]
        clean_text = re.sub("\[\[(.+?)\]\]", "\\1", clean_text)  # [[links]]
        clean_text = re.sub("(align|class|style)\s*=\s*(\".+?\"|[^\"].+? )", "", clean_text)  # common in wikitables (align|class|style) etc

        orig = clean_text
        same = False
        while not same:
            clean_text = re.sub("\{\{[^\}]*?\}\}", "", clean_text)  # templates
            same = clean_text == orig
            orig = clean_text
        clean_text = re.sub("\[https?:.*?\]", "", clean_text)  # external links
        return clean_text

    def run(self):
        global MIN_SIZE, DEBUG_MODE
        if self.report_page is None:
            orig_report = [""]
        else:
            try:
                orig_report = self.report_page.get()
                orig_report = orig_report.split('==', 1)
            except:
                orig_report = [""]
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']
        uploads = []
        ignore_regex = re.compile(local_messages['ignore_summary'], re.I)
        for p, new_rev, prev_rev in self.generator:
            pywikibot.output('Title: %s' % p.title())
            pywikibot.output('\tPrev: %i\tNew:%i' % (prev_rev, new_rev))
            try:
                self.site.loadrevisions(p,
                                        getText=True,
                                        revids=[new_rev, prev_rev])
                old = "" if prev_rev == 0 else self.remove_wikitext(p.getOldVersion(prev_rev))
                new = self.remove_wikitext(p.getOldVersion(new_rev))
                editor = p._revisions[new_rev].user  # TODO: is there a non private access to user in revisions?
                comment = p._revisions[new_rev].comment
                diff_date = p._revisions[new_rev].timestamp
                # skip edits with specific comments
                if ignore_regex.match(comment):
                    continue
            except:
                pywikibot.output("Error occurred - skipping")
                continue

            diffy = difflib.SequenceMatcher()
            diffy.set_seqs(old, new)
            diff = [''.join(new[after_start:after_end]) for opcode, before_start, before_end, after_start, after_end in
                    diffy.get_opcodes() if opcode in ['insert']]

            # clean some html/wikitext from the text before sending to server...
            # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
            added_lines = pywikibot.removeHTMLParts(u'\n'.join(diff), keeptags=[])
            if len(added_lines) > MIN_SIZE:
                pywikibot.output('Uploading to server')
                pywikibot.output('-------------------')
                if DEBUG_MODE:
                    continue
                try:
                    upload_id = self.upload_diff(added_lines.encode('utf8'), p.title(), "/%i" % new_rev)
                    uploads.append(({
                                        u'title': p.title(),
                                        u'user': editor,
                                        u'new': new_rev,
                                        u'old': prev_rev,
                                        u'diff_date': diff_date}, upload_id, added_lines))
                except:
                    print('Skipping - due to error')
                    continue
            else:
                pywikibot.output('Change is too small - skipping')

        pywikibot.output('Polling uploads')
        reports_source = [{'source': self.poll_response(upload_id, rev_details['title'], added_lines),
                           'diffTemplate': local_messages['template-diff']} for rev_details, upload_id, added_lines in uploads]

        report_template = u"""
|- valign="top"
| [[{title}]]
| {diff_date} ({{{{{diffTemplate}|{title}|{new}|{old}}}}}, [{{{{fullurl:{title}|action=history}}}} {{{{subst:MediaWiki:History}}}}])
| [[User:{user}|]] ([[User talk:{user}|{{{{subst:MediaWiki:Talk}}}}]])
| style="font-size:small" |
{source}
|"""
        reports_details = [dict(details[0].items() + source.items()) for details, source in zip(uploads, reports_source)
                           if len(source['source']) > 0]
        reports_details = [report_template.format(**rep) for rep in reports_details]

        if len(reports_details) > 0:
            reports = u"""== ~~~~~ ==
{| class="wikitable sortable" style="width: 80%%;margin:auto;"
! width="15%%" | %s !! width="10%%" | %s !! width="10%%" | %s !! %s !! %s
%s
|}
""" % (local_messages['table-title'], local_messages['table-diff'], local_messages['table-editor'],
       local_messages['table-source'], local_messages['table-status'], ''.join(reports_details))

            if len(orig_report) == 2:
                reports = orig_report[0] + reports + "\n==" + orig_report[1]
            else:
                reports = orig_report[0] + reports

            pywikibot.output(reports)
            if self.report_page is not None:
                self.report_page.put(reports, "Update")
        else:
            pywikibot.output('No violation found!')


def db_changes_generator(site, talk_template=None, days=0.125):
    """
    Generator for changes in specific wikiproject
    """
    pywikibot.output('Connecting to %s' % (dbsettings.host % site.dbName()))
    conn = MySQLdb.connect(host=dbsettings.host % site.dbName(),
                           db=dbsettings.dbname % site.dbName(),
                           read_default_file=dbsettings.connect_file)
    date_limit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d%H%M%S')
    cursor = conn.cursor()
    if talk_template is None:
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
""" % talk_template
    ignore_summary = messages[site.lang]['ignore_summary'] if site.lang in messages else ''
    print('''/* copyright */
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
        rc_comment not rlike '%s'
    order by  rc_new_len-rc_old_len desc
''' % (talk_sql, date_limit, ignore_summary.encode('utf-8')))
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
        rc_new_len-rc_old_len>500/* and
        rc_comment not like '%%rollback%%'*/
    order by  rc_new_len-rc_old_len desc
''' % (talk_sql, date_limit))
    changes = []
    for curid, prev_id, title, diffSize in cursor.fetchall():
        changes.append((pywikibot.Page(site, title.decode('utf-8')), curid, prev_id))
    pywikibot.output('Num changes: %i' % len(changes))
    return changes


def parse_blacklist(page_name):
    """
    Backlist format: # to end is comment. every line is regex.
    """
    page = pywikibot.Page(pywikibot.getSite(), page_name)
    blackList=page.get()
    blacklist_sites = [re.sub('(#|==).*$', '', line).strip() for line in blackList.splitlines()[1:]]
    blacklist_sites = filter(lambda line: len(line)>0, blacklist_sites)
    reblacklist = []
    for ig_site in blacklist_sites:
        try:
            reblacklist.append(re.compile(ig_site))
        except Exception as e:
            print('Error for regex:' + ig_site)
            print(e)
    return reblacklist


def main(*args):
    """
    Handle arguments using standard pywikibot args handling and then runs the bot main functionality.

    """
    global ignore_sites, DEBUG_MODE
    report_page = None

    report_page = None
    generator = None
    for arg in pywikibot.handleArgs(*args):
        site = pywikibot.getSite()
        if arg.startswith('-talkTemplate:'):
            generator = db_changes_generator(site, talk_template=arg[len("-talkTemplate:"):])
        elif arg.startswith('-recentchanges:'):
            generator = db_changes_generator(site, days=float(arg[len("-recentchanges:"):]))
        elif arg.startswith('-api_recentchanges:'):
            source = RecentChangesPageGenerator(namespaces=[0], showBot=False,
                                                total=int(arg[len("-api_recentchanges:"):]), changetype=['edit'],
                                                showRedirects=False)
            generator = [(p, p.latestRevision(), p.previousRevision()) for p in source]
        elif arg.startswith('-report:'):
            report_page = arg[len("-report:"):]
        elif arg.startswith('-debug_mode'):
            DEBUG_MODE = True
            print('DEBUG MODE!')
        elif arg.startswith('-blacklist:'):
            ignore_sites = parse_blacklist(arg[len("-blacklist:"):])
            #print('Blacklist:'+'\n'.join([x.pattern for x in ignore_sites]))

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
