#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""
Script to catch copyright violations.
The script use DB or API to get diffs, and sends inserts and replacements to an external service 
to find copyright violations.

Output can be to console (default) or to wiki page 

Command line options:
    -report:Page            page name to write report to.
    -talkTemplate:Foo       Run on diffs of a pages with talk page containing {{Foo}}
    -pagesLinkedFrom:Bar    Run on diffs of pages linked from the page [[Wikipedia:Bar]]
    -recentchanges:X        Number of days to fetch recent changes. For 12 hours set 0.5.
    -blacklist:Page         page containing a blacklist of sites to ignore (Wikipedia mirrors)
                                [[User:EranBot/Copyright/Blacklist]] is collaboratively maintained
                                blacklist for English Wikipedia.

&params;

Usage examples:

Report on WikiProject Medicine articles AND pages linked from the [[Wikipedia:Education noticeboard/Incidents]]
    python plagiabot.py -lang:en -report:"Wikipedia:MED/Copyright" -talkTemplate:"WikiProject_Medicine" -pagesLinkedFrom:"Education_noticeboard/Incidents" -blacklist:"User:EranBot/Copyright/Blacklist"

Report on possible violations only on Wikiproject Medicine related articles:
    python plagiabot.py -lang:en -report:"Wikipedia:MED/Copyright" -talkTemplate:"WikiProject_Medicine"

Report on possible violations in the last 3 days to console:
    python plagiabot.py -recentchanges:3

Report on possible violations in the top 100 recent changes (no DB access required):
    python plagiabot.py -api_recentchanges:100
"""
import time
import datetime
import difflib
try:
    import oursql as MySQLdb
except:
    import MySQLdb
import re
import uuid
try:
    from xmlrpc import client as xmlrpclib
except:
    import xmlrpclib
import requests
import urllib

import pywikibot
from pywikibot import pagegenerators, config
from plagiabot_config import ithenticate_user, ithenticate_password
import report_logger

docuReplacements = {
    '&params;':     pagegenerators.parameterHelp,
}

db_host='{0}.labsdb'  # host name of the db (default to format in wmflabs)

MIN_SIZE = 500  # minimum length of added text for sending to server
MIN_PERCENTAGE = 50
WORDS_QUOTE = 50
MAX_AGE = 1  # how many days worth of recent changes to check
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
        'ignore_summary': '\[*(Reverted|Undid revision|rv$)',
        'rollback_of_summary': 'Reverted .*?edits? by (\[\[User:)?{0}|Undid revision {1}|Reverting possible vandalism by (\[\[User:)?{0}'
    },
    'he': {
        'table-title': 'כותרת',
        'table-editor': 'עורך/עורכת',
        'table-diff': 'הבדל',
        'table-status': 'מצב',
        'template-diff': 'הבדל',
        'table-source': 'מקורות',
        'update-summary': 'עדכון',
        'ignore_summary': '(שוחזר מעריכות של|ביטול גרסה|שחזור עריכות)',
        'rollback_of_summary': 'שוחזר מעריכ(ה|ות) של (\[\[User:|\[\[משתמש:)?{0}|ביטול גרסה {1}'
   },
   'fr': {
        'table-title': 'Titre',
        'table-editor': 'Editeur',
        'table-diff': 'Diff',
        'table-status': 'Status',
        'template-diff': u'diff',
        'table-source': 'Source',
        'update-summary': 'Bot: Mise à jour',
        'ignore_summary': '\[*(Annulation|R[ée]vocation|Vandalisme|Retour|revert|rv$)',
        'rollback_of_summary': '(Annulation|R[ée]vocation|Vandalisme|Retour).*?éditions? .*?par (\[\[(User|Utilisateur|Contributions):)?{0}|Annulation de l\'édition {1}|[[WP:FOI|bonne foi]] de (\[\[(User|Utilisateur|Contributions):)?{0}'
    },
    'pt': {
        'table-title': 'Título',
        'table-editor': 'Editor',
        'table-diff': 'Diff',
        'table-status': 'Status',
        'template-diff': u'Diff',
        'table-source': 'Fonte',
        'update-summary': 'Atualização',
        'ignore_summary': '\[*(Revertido|Revisão desfeita|rv$)',
        'rollback_of_summary': 'Revertidas .*?edições? de (\[\[Usuário(a):)?{0}|Revisão desfeita {1}|Revertendo possível vandalismo de (\[\[Usuário(a):)?{0}'
    }

}
DEBUG_MODE = False
ignore_sites = [re.compile('\.wikipedia\.org'), re.compile('he-free.info'),
                re.compile('lrd.yahooapis.com')]
wikiEd_pages = set()
def log(msg):
    pywikibot.log(msg)
    #print(msg)

class PlagiaBot(object):
    def __init__(self, site, generator, report_page=None, report_log=report_logger.ReportLogger()):
        self.generator = generator

        # variables for connecting to server
        self.server = None
        self.folder = None
        self.sid = None
        self.site = site
        self.report_page = None if report_page is None else pywikibot.Page(self.site, report_page)
        self.uploads = []
        self.last_uploads_status = time.time()
        self.report_log = report_log

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
        pywikibot.output("\tUpload text to server...")

        SUBMIT_TO_GENERATE_REPORT = 1
        SUBMIT_TO_STORE_IN_REPOSITORY = 2
        SUBMIT_TO_STORE_IN_REPOSITORY_AND_GENERATE_REPORT = 3

        submit_response = self.server.document.add({
            'sid': self.sid,
            'submit_to': SUBMIT_TO_GENERATE_REPORT,
            'folder': self.folder['id'],
            'uploads': [{'title': '{}{}'.format(title, diff_id),#'%s - %i' % (title, uuid.uuid4()),
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

    def uploads_ready(self):
        if time.time()-self.last_uploads_status < 45:
            return False
        pywikibot.output('Checking uploads ({}). '.format(len(self.uploads)), newline=False)

        for rev_details, upload_id, added_lines in self.uploads[::-1]:
            document_get_response = self.server.document.get({'id': upload_id, 'sid': self.sid})
            if (document_get_response['status'] != 200):
                raise Exception('Error retreving document {}'.format(upload_id))
            document = document_get_response['documents'][0]
            pending = document['is_pending']
            if pending:
                pywikibot.output('Waiting')
                self.last_uploads_status = time.time()
                return False
        pywikibot.output('ready')
        return True


    def poll_response(self, upload_id, article_title, added_lines, rev_id):
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
        if 'parts' not in document:
            pywikibot.output('Error getting parts of document. Rev id: ' + str(rev_id))
            return '', 0
        for part in document['parts']:
            # not sure if there is always a single part, so looping over it instead.
            pywikibot.output("Part #%i has a %i%% match. Getting details..." % (part['id'], part['score']))

            try:
                report_get_response = self.server.report.get({'id': part['id'], 'sid': self.sid})
                assert (report_get_response['status'] == 200)
                pywikibot.output("Details are available on %s" % (report_get_response['report_url']))
                report_sources_response = self.server.report.sources({'id': part['id'], 'sid': self.sid})
                assert (report_sources_response['status'] == 200)
            except Exception as e:
                # silently drop this entry
                pywikibot.output('Err ' + e.message)
                return '', 0

            report = []
            sources = [cp_source for cp_source in report_sources_response['sources'] if
                       'linkurl' in cp_source and not any([ig.search(cp_source['linkurl']) for ig in ignore_sites])]
            num_sources = 0
            pywikibot.output("%i non ignore sites found" % (len(sources)))
            for source in sources:
                pywikibot.output(source['linkurl'])
                if int(source['percent']) < MIN_PERCENTAGE:
                    # pywikibot.output('Not enough similarity '+ str(source['percent']))
                    continue
                
                hint_text = ''
                try:
                    if source['linkurl'].lower() in added_lines.lower():  # the source is mentioned in the added text
                        hint_text = '<span class="success">citation</span>'
                    else:
                        req_source = requests.get(source['linkurl'])
                        if req_source.status_code == 200:
                            title_encode = urllib.quote(article_title)
                            mirror_re = re.compile('(wikipedia.org/w(iki/|/index.php\?title=)(%s|%s)|material from the Wikipedia article|From Wikipedia|source: wikipedia)' % (
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
                            elif len(req_source.text)<5 or any(re.findall('domain is for sale|buy this domain|get your domain name', req_source.text, re.I)) or \
                                    (re.search('<html', req_source.text, re.I) and
                                        len(re.findall('<a [^>]*>', req_source.text, re.I)) < 10):
                                hint_text = '<span class="error">Low quality site</span>'
                                pywikibot.output('Low quality site')
                                continue  #  low quality sites
                        elif req_source.status_code in [403,404, 500]:
                            continue  # low quality source - ignore
                    num_sources += 1
                except requests.exceptions.ConnectionError:
                    hint_text = '<span class="error">connection error</span>'
                    pywikibot.output('Connection error to site')
                    continue  # we trust it enough by now to just skip those results
                except Exception as e:
                    pywikibot.output('Err ' + e.message)
                    num_sources += 1
                    pass
                compare_link = '//tools.wmflabs.org/copyvios?lang={{subst:CONTENTLANG}}&project={{lc:{{ns:Project}}}}&title=&oldid='+str(rev_id)+'&action=compare&url='+source['linkurl']
                report.append("* %s % 3i%% %i words at [%s %s] %s<div class=\"mw-ui-button\">[%s Compare]</div>" % (
                    source['collection'][0], source['percent'], source['word_count'], source['linkurl'], source['linkurl'][:80], hint_text, compare_link))
                if num_sources == 3:
                        break
            report = '<div class="mw-ui-button">[%s report]</div>\n'%DIFF_URL%part['id']+'\n'.join(report) if len(report)>0 else ''
            pywikibot.output(report)
            return report, part['id']

    def remove_wikitext(self, text):
        # clean some html/wikitext from the text before sending to server...
        # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
        global WORDS_QUOTE
        #remove refis
        if text is None or len(text) == 0: return ''
        refs = re.findall('<ref(?: .+?)?>(.*?)</ref>', text)
        for ref in refs:
            if ref.count(' ') < WORDS_QUOTE:
                text = text.replace(ref, '')
        clean_text = pywikibot.textlib.removeHTMLParts(text, keeptags=[])
        clean_text  =re.sub("\[\[Category:.+?\]\]", "", clean_text)  # categories
        clean_text = re.sub("\[\[[^\[\]]+\|([^\[\]]+)\]\]", "\\1", clean_text)  # [[link|textlink]]
        clean_text = re.sub("\[\[(.+?)\]\]", "\\1", clean_text)  # [[links]]
        clean_text = re.sub("\n(==+)\s*([^=]+)\s*\\1","\n\\2", clean_text) # remove == from titles
        clean_text = re.sub("'''([^']+)'''","\\1", clean_text) # remove ''' bold
        clean_text = re.sub("''([^']+)''","\\1", clean_text) # remove '' italics
        clean_text = re.sub("(align|class|style)\s*=\s*(\".+?\"|[^\"].+? )", "", clean_text)  # common in wikitables (align|class|style) etc
        clean_text = re.sub("\n\\|-.{0,20}","", clean_text) # clean wikitables new lines
        clean_text = re.sub("(\n\\|}|\n\\{\\| *[^\n]*)","", clean_text) # clean open/end of wikitables
        clean_text = re.sub("\n![^\\|]+\\|","\n", clean_text) # clean table headers
        clean_text = re.sub("\s*\\| *\w+ *= *(\"?#?[A-Za-z0-9]+\"?|\n)","", clean_text) # clean technical definitions (in templates and tables)
        clean_text = re.sub("(?:\\| *)+","|", clean_text) # compact
        clean_text = re.sub("\\n\\| *","\\n", clean_text) # trim |
        clean_text = re.sub("(File|Image):[^\\.]+?\\.(jpg|png|pdf|svg)","", clean_text, re.I) # file names

        orig = clean_text
        same = False
        while not same:
            clean_text = re.sub("\{\{[^\{]*?\}\}", "", clean_text, re.M)  # templates
            same = clean_text == orig
            orig = clean_text
        clean_text = re.sub("\[https?:.*?\]", "", clean_text)  # external links

        return clean_text

    def was_rolledback(self, page, new_rev, added_lines):
        rolledback = False
        
        self.site.loadrevisions(page, startid=new_rev,rvdir=True)

        #Check whether the add lines exists in the current version or not
        current_text = pywikibot.textlib.removeHTMLParts(self.remove_wikitext(page.text))
        current_check = difflib.SequenceMatcher(None, added_lines, current_text)
        current_match = current_check.find_longest_match(0,len(added_lines),0,len(current_text))
        if float(current_match.size)/len(added_lines) > 0.8:
            pywikibot.output("Added lines don't exist in current version - skipping")
            return True

        # alternatively, look for rollback of that revision
        editor = page._revisions[new_rev].user
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']
        try:
            reverted_edit = re.compile(local_messages['rollback_of_summary'].format(editor, new_rev))
            for rev in page._revisions:
                user = page._revisions[rev].user
                comment = page._revisions[rev].comment
                is_the_editor = editor in comment
                is_revert = reverted_edit.match(comment)
                if is_revert and is_the_editor:
                    #print('Was rolledback by {}: {}'.format(user,comment))
                    rolledback = True
        except:
            pass
        return rolledback

    def remove_moved_content(self, page, prev_rev, content, comment):
        global MIN_SIZE
        if prev_rev != 0:
            self.site.loadrevisions(page, startid=prev_rev, getText=True, total=3)
            for rev in page._revisions:
                if rev>=prev_rev: continue
                old_content = self.remove_wikitext(page.getOldVersion(rev))
                content = u'\n'.join([line for line in content.split(u'\n') if line not in old_content])

        if len(content) < MIN_SIZE:
            return content

        # moved content indicated from the comment itself
        possible_articles = re.findall('\[\[(.+?)\]\]', comment)
        for pos_article in possible_articles:
            pos_page = pywikibot.Page(self.site, pos_article)
            try:
                self.site.loadrevisions(pos_page, startid=prev_rev, getText=True, total=2)
                for rev in pos_page._revisions:
                    old_content = self.remove_wikitext(pos_page.getOldVersion(rev))
                    content = u'\n'.join([line for line in content.split(u'\n') if line not in old_content])
            except:
                pass

        # also invoke search to look in other articles?
        return content

    def process_changes(self):
        global MIN_SIZE, DEBUG_MODE, WORDS_QUOTE
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']
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
            except Exception as e:
                pywikibot.output("Error occurred - skipping: %s" % e.message)
                continue

            diffy = difflib.SequenceMatcher()
            diffy.set_seqs(old, new)
            diff = [''.join(new[after_start:after_end]) for opcode, before_start, before_end, after_start, after_end in
                    diffy.get_opcodes() if opcode in ['insert']]
            diff = [new_t for new_t in u'\n'.join(diff).split(u'\n') if new_t not in old and ' ' in new_t] # remove text appeared in original or very small addition
            # avoid reoccurence
            added_set = set()
            diff_clean = []
            for new in diff:
                if new in added_set: continue
                diff_clean.append(new)
                added_set.add(new)
            diff = diff_clean

            # remove list of facts
            diff = [line for line in diff if not re.match('^(\S+(\s|$)){1,4}$', line.strip('* |'))]

            # clean some html/wikitext from the text before sending to server...
            # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
            added_lines = pywikibot.textlib.removeHTMLParts(u'\n'.join(diff), keeptags=[])

            #pywikibot.output(added_lines)
            if len(added_lines) < MIN_SIZE:
                pywikibot.output('\tDelta too small (after removing HTML)')
                continue

            # remove moved content (also avoids mirrors)
            added_lines = self.remove_moved_content(p, prev_rev, added_lines, comment) 

            added_lines = u'. '.join([new_t for new_t in added_lines.split(u'. ') if new_t not in old]) # remove text appeared in original
            #remove quotation (for small quotes)
            quotes = re.findall('".*?"[ ,\.;:<\{]', added_lines)
            for quote in quotes:
                if quote.count(' ') < WORDS_QUOTE:
                    added_lines = added_lines.replace(quote, '')

            if len(added_lines) > MIN_SIZE and (prev_rev==0 or not self.was_rolledback(p, new_rev, added_lines) and len(re.split('\s', added_lines)) > 20):
                if DEBUG_MODE: # dont upload to server in debug mode
                    continue
                try:
                    upload_id = self.upload_diff(added_lines.encode('utf8'), p.title(), "/%i" % new_rev)
                    self.uploads.append(({
                                        u'title': p.title(),
                                        u'user': editor,
                                        u'new': new_rev,
                                        u'old': prev_rev,
                                        u'ns': p.namespace(),
                                        u'title_no_ns': p.title(withNamespace=False),
                                        u'diff_date': diff_date}, upload_id, added_lines))
                except Exception as ex:
                    print('Skipping - due to error: {}'.format(ex))
                    # TODO: reconnect to server?
                    continue
            else:
                pywikibot.output('\tDelta too small - skipping')

    def report_uploads(self):
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']

        pywikibot.output('Polling uploads')
        reports_source = [self.poll_response(upload_id, rev_details['title'], added_lines, rev_details['new']) for rev_details, upload_id, added_lines in self.uploads]
        reports_source = [{'report_id': report_id, 'source': report_source} for report_source, report_id in reports_source] 
        # Define the format of an individual report row.
        report_template = u"""
{{{{plagiabot row2 | article = {title} | tags= {tags} | timestamp = {diff_date} | diff = {new} | oldid = {old} | user = {user} | details =
{source}
| status =
}}}}
== ==
"""
        reports_details = [dict(details[0].items() + source.items()) for details, source in zip(self.uploads, reports_source)
                           if len(source['source']) > 0]
        # add tags by associated wikiprojects
        for report in reports_details:
            report['tags'] = get_page_tags(self.site, report['title'])

        for rep in reports_details:
            self.report_log.add_report(rep['new'], rep['diff_date'], rep['title_no_ns'], rep['ns'], rep['report_id'], rep['source'])
        reports_details = [report_template.format(**rep) for rep in reports_details]

        if len(reports_details) == 0:
            pywikibot.output('No violation found!')
            return 
        print('{} violations found'.format(len(reports_details)))
        seperator = '\n{{plagiabot row'#'\n|- valign="top"\n'
        if self.report_page is None:
            orig_report = [""]
        else:
            try:
                orig_report = self.report_page.get(force=True)
                orig_report = orig_report.split(seperator, 1)
            except:
                orig_report = [""]
        reports = u"""
{| class="mw-datatable sortable" style="width: 90%%;margin:auto;"
! style="width:15%%" | %s !! style="width:10%%" | %s !! style="width:50px" | %s !! %s !! style="width:150px;" |%s
|- valign="top"
%s
|}
""" % (local_messages['table-title'], local_messages['table-diff'], local_messages['table-editor'],
   local_messages['table-source'], local_messages['table-status'], ''.join(reports_details))

        pywikibot.output(''.join(reports_details))
        if len(orig_report) == 2:
            reports = orig_report[0] + ''.join(reports_details) + seperator + orig_report[1]
        else:
            reports = orig_report[0] + reports
        # save to report page is specified
        if self.report_page is None:
            return
        try_save = True
        while try_save:
            try:
                try_save = False
                self.report_page.put(reports, "Update")
            except pywikibot.SpamfilterError:
                pywikibot.output('spam filter error')
            except PageSaveRelatedError:
                pywikibot.output('page save related error')
            except pywikibot.EditConflict:
                try_save = True
                orig_report = self.report_page.get(force=True)
                orig_report = orig_report.split(seperator, 1)
                if len(orig_report) == 2:
                    reports = orig_report[0] + ''.join(reports_details) + seperator + orig_report[1]
                else:
                    reports = orig_report[0] + reports


    def run(self): 
        self.process_changes()
        self.report_uploads()

class PlagiaBotLive(PlagiaBot):
    def __init__(self, site, report_page=None, use_stream=False, report_log=report_logger.ReportLogger(), run_timeout = 14400):
        super(PlagiaBotLive, self).__init__(site, [], report_page, report_log)
        self.rcthreshold = 10
        self.use_stream = use_stream
        local_messages = messages[self.site.lang] if self.site.lang in messages else messages['en']
        self.ignore_regex = re.compile(local_messages['ignore_summary'], re.I)
        self.end_time = datetime.datetime.now() + datetime.timedelta(0, run_timeout)

    def page_filter(self, page):
        global wikiEd_pages
        rcinfo = page._rcinfo
        if rcinfo['type'] != 'edit': return False  # only edits
        if rcinfo['bot']: return False # skip bot edits
        if (rcinfo['namespace'] not in [0, 118]) and page.title() not in wikiEd_pages: return False  # only articles+drafts
        if 'length' in rcinfo:
            new_size = rcinfo['length']['new']
            old_size = rcinfo['length']['old'] or 0
            diff_size = new_size - old_size
        else:
            diff_size = rcinfo['diff_bytes']
        if diff_size < MIN_SIZE: return False  # skip small/minor changes
        if self.ignore_regex.match(rcinfo['comment']): return False  # skip rollbacks
        return True
   
    def run(self):
        global MIN_SIZE
        self.generator = []
        log('Starting live bot')
        filter_gen = lambda gen: [p for p in gen if self.page_filter(p)]
        if self.use_stream:
            live_gen = pagegenerators.LiveRCPageGenerator(self.site)
            live_gen = filter_gen(live_gen)
        else:
            from IRCRCListener import irc_rc_listener
            live_gen = (p for p in irc_rc_listener(self.site, filter_gen))
        pending_checks = []
        reconnect_index = 100
        try:
            for page in live_gen:
                rcinfo = page._rcinfo
                #log('Adding page:' + page.title())
                # TODO: remove rolledback edits from generator
                pywikibot.output('Page in buffer: {}'.format(len(pending_checks)))
                pending_checks.append((page, rcinfo['revision']['new'], rcinfo['revision']['old'] or 0))
                if self.end_time < datetime.datetime.now():
                    raise KeyboardInterrupt
                if len(pending_checks) < self.rcthreshold or not self.uploads_ready(): continue # move to next edit if not enough edits accomulated
                # handle uploads or send new changes to process
                if len(self.uploads) > 0:
                    pywikibot.output('reporting uploads')
                    self.report_uploads()  # report checked edits
                    print('reported')
                    self.uploads = []
                    reconnect_index -= 1
                    if reconnect_index == 0:
                        pywikibot.output('Reconnect after many uploads' )
                        self._init_server()
                        reconnect_index = 100
                else:
                    pywikibot.output('checking pending')
                    self.generator = pending_checks
                    log('checking pending')
                    self.process_changes()
                    pending_checks = []
        except KeyboardInterrupt:
            pywikibot.output('handling uploaded changes')
            while not self.uploads_ready(): continue
            # handle uploads or send new changes to process
            if len(self.uploads) > 0:
                self.report_uploads()  # report checked edits
                raise
 
def articles_from_talk_template(talk_template):
    """
    Given a page in the Project: (Wikipedia:) namespace, compose the sql query for finding all articles linked from the page. The output can then be joined with additional sql queries to select recent changes to those articles.
    """
    # Take a Project namespace page title, without namespace prefix, and find all the articles (or pages in another namespace) linked from it.
    list_sql = """
    select page_title
    from
        templatelinks
    inner join
                page 
        on 
                page_id=tl_from and
                page_namespace=1
        where 
                tl_title='%s' and
                tl_namespace=10 and tl_from_namespace=1
                """ % (talk_template)

    return list_sql

def articles_from_list(page_of_pages, namespace=0):
    """
    Given a page in the Project: (Wikipedia:) namespace, compose the sql query for finding all articles linked from the page. The output can then be joined with additional sql queries to select recent changes to those articles.
    """
    
    # Take a Project namespace page title, without namespace prefix, and find all the articles (or pages in another namespace) linked from it.
    list_sql = """
    select pl_title as page_title
    from
        pagelinks
    inner join
                page 
        on 
                page_id=pl_from
        where 
                pl_from= ( select page_id from page where page_title='%s' and page_namespace=%s  )
""" % (page_of_pages, namespace)

    return list_sql

def db_changes_generator(site, talk_template=None, page_of_pages=None, days=1, namespace=0):
    """
    Generator for changes to a set of pages
    """
    pywikibot.output('Connecting to %s' % (db_host.format(site.dbName())))
    conn = MySQLdb.connect(host=db_host.format(site.dbName()),
                           db=config.db_name_format.format(site.dbName()),
                           read_default_file=config.db_connect_file)
    date_limit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d%H%M%S')
    cursor = conn.cursor()
    
    sql_page_selects = []
    
    # If page_of_pages parameter is given, get the query for the list of linked pages; otherwise, get an empty placeholder query.
    if page_of_pages:
        list_of_pages = articles_from_list(page_of_pages, 4)
        sql_page_selects.append(list_of_pages)
    
    # If talk_template parameter is given, get the query for the list of linked pages; otherwise, get an empty placeholder query.
    if talk_template:
        templated_pages = articles_from_talk_template(talk_template)
        sql_page_selects.append(templated_pages)

    if len(sql_page_selects)==0:
        sql_join = ""
    else:
        # If there are multiple selects for sets of page titles, we want to get the union of these selects.
        union_of_lists = " UNION ".join(x for x in sql_page_selects)
        sql_join = """
        inner join
            ( %s )
            pages
        on
            rc_title=page_title
            """ % union_of_lists

    # Use the select for a set of pages to find changes to compose a query for changes to those pages
    ignore_summary = messages[site.lang]['ignore_summary'] if site.lang in messages else messages['en']['ignore_summary']
    ignore_summary = ignore_summary.replace('\\','\\\\')#.encode('utf8')
    query = '''
/* plagiabot */
        select max(rc_this_oldid), min(rc_last_oldid), rc_title, max(rc_new_len-rc_old_len) as diffSize
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
                rc_namespace=%s and
                rc_timestamp > %s and
/*                rc_new_len-rc_old_len>500 and*/
                rc_comment not rlike '%s'
            /*order by  rc_new_len-rc_old_len desc*/
        group by rc_title
        having max(rc_new_len-rc_old_len)>500
        ''' % (sql_join, namespace, date_limit, ignore_summary)

    print(query)
    # Run the query
    cursor.execute(query)
    changes = []
    for curid, prev_id, title, diffSize in cursor.fetchall():
        changes.append((pywikibot.Page(site, title.decode('utf-8')), curid, prev_id))
    pywikibot.output('Num changes: %i' % len(changes))
    return changes

def get_page_tags(site, page_name):
    global wikiEd_pages
    page = pywikibot.Page(site, page_name)
    talk_page = page.toggleTalkPage()
    talk_templates = [tp.title(withNamespace=False) for tp in talk_page.templates()]
    projects = [tp for tp in talk_templates if re.match('WikiProject ', tp) and '/' not in tp]
    if page.title() in wikiEd_pages:
        projects.append('WikiEd')
    return ';'.join(projects)


def parse_blacklist(page_name):
    """
    Backlist format: # to end is comment. every line is regex.
    """
    page = pywikibot.Page(pywikibot.Site('meta', 'meta'), page_name)
    try:
        blackList=page.get()
    except pywikibot.exceptions.NoPage:
        raise Exception('The blacklist page named "%s" could not be found on metawiki.' % page_name)
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

def fill_wikiEd_pages(site):
    global wikiEd_pages
    wikiEd_current = pywikibot.Page(site, 'Wikipedia:Education program/Dashboard/current articles')
    wikiEd_pages = set(map(lambda p: p.title(), wikiEd_current.linkedPages()))
    

def main(*args):
    """
    Handle arguments using standard pywikibot args handling and then runs the bot main functionality.

    """
    global ignore_sites, DEBUG_MODE
    report_page = None
    generator = None
    talk_template = None
    page_of_pages = None
    days = None
    namespace = 0
    live_check = False
    genFactory = pagegenerators.GeneratorFactory()
    report_log = report_logger.ReportLogger()
    for arg in pywikibot.handle_args(args):
        site = pywikibot.Site()
        if arg.startswith('-talkTemplate:'):
            talk_template=arg[len("-talkTemplate:"):]
        elif arg.startswith('-pagesLinkedFrom:'):
            page_of_pages=arg[len("-pagesLinkedFrom:"):]
        elif arg.startswith('-WikiEd'):
            fill_wikiEd_pages(site)  # init wikiEd pages collection
        elif arg.startswith('-live:'):
            live_check = True
        elif arg.startswith('-recentchanges:'):
            days=float(arg[len("-recentchanges:"):])
        elif arg.startswith('-api_recentchanges:'):
            source = pagegenerators.RecentChangesPageGenerator(namespaces=[0], showBot=False,
                                                total=int(arg[len("-api_recentchanges:"):]), changetype=['edit'],
                                                showRedirects=False)
            generator = [(p, p.latestRevision(), p.previousRevision()) for p in source]
        elif arg.startswith('-report:'):
            report_page = arg[len("-report:"):]
        elif arg.startswith('-debug_mode'):
            DEBUG_MODE = True
            print('DEBUG MODE!')
        elif arg.startswith('-reportlogger'):
            report_log = report_logger.DbReportLogger(pywikibot.Site())
            print('using report logger')
        elif arg.startswith('-blacklist:'):
            ignore_sites = parse_blacklist(arg[len("-blacklist:"):])
        elif genFactory.handleArg(arg):
            # general page generators for checking the latest revision
            gen = genFactory.getCombinedGenerator()
            gen = pagegenerators.PreloadingGenerator(gen)
            generator = [(p, p.latestRevision(), 0) for p in gen if p.exists()]

    if (not generator) and (talk_template or page_of_pages or days):
        if not days:
            days = MAX_AGE
        generator =  db_changes_generator(site, talk_template, page_of_pages, days, namespace)
    if generator is None and not live_check:
        pywikibot.showHelp()
    else:
        if live_check:
            log('running live')
            bot = PlagiaBotLive(pywikibot.Site(), report_page , report_log=report_log)
        else:
            log('running non live')
            bot = PlagiaBot(pywikibot.Site(), generator, report_page, report_log=report_log)
        bot.run()


if __name__ == "__main__":
    try:
        main()
    except:
        import traceback
        traceback.print_exc()
        pywikibot.stopme()

