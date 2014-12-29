#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Script to catch copyright violations.
The script use DB or API to get diffs, and sends inserts and replacements to an external service 
to find copyright violations.

Output can be to console (default) or to wiki page 

Command line options:
    -report:Page        page name to write report to.
    -talkTemplate:Foo   Run on diffs of a pages with talk page containing {{Foo}}
    -recentchanges:X    Number of days to fetch recent changes. For 12 hours set 0.5.
    -blacklist:Page     page containing a blacklist of sites to ignore (Wikipedia mirrors)
                            [[User:EranBot/Copyright/Blacklist]] is collaboratively maintained
                            blacklist for English Wikipedia.

&params;

Usage examples:

Report on possible violations in Wikiproject Medicine related articles:
    python plagiabot.py -lang:en -report:"Wikipedia:MED/Copyright" -talkTemplate:"WikiProject_Medicine"

Report on possible violations in the last 3 days to console:
    python plagiabot.py -recentchanges:3

Report on possible violations in the top 100 recent changes (no DB access required):
    python plagiabot.py -api_recentchanges:100
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
import urllib
from plagiabot_config import ithenticate_user, ithenticate_password
from pywikibot import pagegenerators

docuReplacements = {
    '&params;':     pagegenerators.parameterHelp,
}

MIN_SIZE = 500  # minimum length of added text for sending to server
MIN_PERCENTAGE = 50
WORDS_QUOTE = 50
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
        'table-title': u'כותרת',
        'table-editor': u'עורך/עורכת',
        'table-diff': u'הבדל',
        'table-status': u'מצב',
        'template-diff': u'הבדל',
        'table-source': u'מקורות',
        'update-summary': u'עדכון',
        'ignore_summary': u'(שוחזר מעריכות של|ביטול גרסה|שחזור עריכות)',
        'rollback_of_summary': u'שוחזר מעריכ(ה|ות) של (\[\[User:|\[\[משתמש:)?{0}|ביטול גרסה {1}'
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
                        if source['linkurl'].lower() in added_lines.lower():  # the source is mentioned in the added text
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
                                elif len(req_source.text)<5 or any(re.findall('domain is for sale|buy this domain|get your domain name', req_source.text, re.I)) or \
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
                    report.append("* %s % 3i%% %i words at [%s %s] %s" % (
                        source['collection'][0], source['percent'], source['word_count'], source['linkurl'], source['linkurl'][:100], hint_text))
                    if num_sources == 3:
                        break
            report = '[%s report]\n'%DIFF_URL%part['id']+'\n'.join(report) if len(report)>0 else ''
            return report

    def remove_wikitext(self, text):
        # clean some html/wikitext from the text before sending to server...
        # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
        global WORDS_QUOTE
        #remove refs
        refs = re.findall('<ref(?: .+?)?>(.*?)</ref>', text)
        for ref in refs:
            if ref.count(' ') < WORDS_QUOTE:
                text = text.replace(ref, '')
        clean_text = pywikibot.textlib.removeHTMLParts(text, keeptags=[])
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
                    print('Was rolledback by {}: {}'.format(user,comment))
                    rolledback = True
        except:
            pass
        return rolledback

    def remove_moved_content(self, page, prev_rev, content, comment):
        self.site.loadrevisions(page, startid=prev_rev, getText=True, total=3)
        for rev in page._revisions:
            if rev>=prev_rev: continue
            old_content = self.remove_wikitext(page.getOldVersion(rev))
            content = u'\n'.join([line for line in content.split(u'\n') if line not in old_content])
            #break

        if len(content) < 500:
            return content

        # moved content indicated from the comment itself
        possible_articles = re.findall('\[\[(.*?)\]\]', comment)
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

    def run(self):
        global MIN_SIZE, DEBUG_MODE, WORDS_QUOTE
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
            diff = [new_t for new_t in u'\n'.join(diff).split(u'\n') if new_t not in old] # remove text appeared in original

            # clean some html/wikitext from the text before sending to server...
            # you may use mwparserfromhell to get cleaner text (but this requires dependency...)
            added_lines = pywikibot.textlib.removeHTMLParts(u'\n'.join(diff), keeptags=[])
            if len(added_lines) > MIN_SIZE:
                # remove moved content (also avoids mirrors)
                added_lines = self.remove_moved_content(p, prev_rev, added_lines, comment) 

            added_lines = u'. '.join([new_t for new_t in added_lines.split(u'. ') if new_t not in old]) # remove text appeared in original

            #remove quotation (for small quotes)
            quotes = re.findall('".*?"[ ,\.;:<\{]', added_lines)
            for quote in quotes:
                if quote.count(' ') < WORDS_QUOTE:
                    added_lines = added_lines.replace(quote, '')

            if len(added_lines) > MIN_SIZE and not self.was_rolledback(p, new_rev, added_lines):
                pywikibot.output('Uploading to server')
                pywikibot.output('-------------------')
                if DEBUG_MODE: #TODO: remove
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
| {diff_date} ({{{{{diffTemplate}|{title}|{new}|{old}}}}}, [{{{{fullurl:{title}|action=history}}}} {{{{subst:MediaWiki:Hist}}}}])
| [[User:{user}|]] ([[User talk:{user}|{{{{subst:MediaWiki:Talk}}}}]])
| style="font-size:small" |
{source}
|"""
        reports_details = [dict(details[0].items() + source.items()) for details, source in zip(uploads, reports_source)
                           if len(source['source']) > 0]
        reports_details = [report_template.format(**rep) for rep in reports_details]

        if len(reports_details) > 0:
            if self.report_page is None:
                orig_report = [""]
            else:
                try:
                    orig_report = self.report_page.get()
                    orig_report = orig_report.split('\n|- valign="top"\n', 1)
                except:
                    orig_report = [""]
            reports = u"""
{| class="mw-datatable sortable" style="width: 90%%;margin:auto;"
! style="width:15%%" | %s !! style="width:10%%" | %s !! style="width:50px" | %s !! %s !! style="width:150px;" |%s
%s
|}
""" % (local_messages['table-title'], local_messages['table-diff'], local_messages['table-editor'],
       local_messages['table-source'], local_messages['table-status'], ''.join(reports_details))

            if len(orig_report) == 2:
                reports = orig_report[0] + ''.join(reports_details) +'\n|- valign="top"\n'+ orig_report[1]
            else:
                reports = orig_report[0] + reports

            pywikibot.output(reports)
            if self.report_page is not None:
                self.report_page.put(reports, "Update")
        else:
            pywikibot.output('No violation found!')

def articles_from_list(site, page_of_pages, namespace=0):
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
                pl_from= ( select page_id from page where page_title='%s' and page_namespace='%s'  )
""" % (page_of_pages, namespace)

    return list_sql

def db_changes_generator(site, talk_template=None, page_of_pages=None, days=1, namespace=0):
    """
    Generator for changes to a set of pages
    """
    pywikibot.output('Connecting to %s' % (dbsettings.host % site.dbName()))
    conn = MySQLdb.connect(host=dbsettings.host % site.dbName(),
                           db=dbsettings.dbname % site.dbName(),
                           read_default_file=dbsettings.connect_file)
    date_limit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d%H%M%S')
    cursor = conn.cursor()
    
    sql_page_selects = []
    
    # If page_of_pages parameter is given, get the query for the list of linked pages; otherwise, get an empty placeholder query.
    if page_of_pages:
        list_of_pages = articles_from_list(site, page_of_pages, namespace)
        sql_page_selects.append(list_of_pages)
    
    # If talk_template parameter is given, get the query for the list of linked pages; otherwise, get an empty placeholder query.
    if talk_template:
        templated_pages = articles_from_talk_template(site, talk_template, namespace)
        sql_page_selects.append(templated_pages)

    if len(sql_page_selects)==0:
        sql_join = ""
    else:
        # If there are multiple selects for sets of page titles, we want to get the union of these selects.
        union_of_lists = " UNION ".join(x for x in sql_page_selects)
        pages = """
        inner join
            ( '%s' )
            pages
        on
            rc_title=page_title
            """ % union_of_lists

    # Use the select for a set of pages to find changes to compose a query for changes to those pages
    query = '''
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
        ''' % (pages, date_limit)

    ignore_summary = messages[site.lang]['ignore_summary'] if site.lang in messages else ''
    print(query)
    
    # Run the query
    cursor.execute(query)
    changes = []
    for curid, prev_id, title, diffSize in cursor.fetchall():
        changes.append((pywikibot.Page(site, title.decode('utf-8')), curid, prev_id))
    pywikibot.output('Num changes: %i' % len(changes))
    return changes

def parse_blacklist(page_name):
    """
    Backlist format: # to end is comment. every line is regex.
    """
    page = pywikibot.Page(pywikibot.Site(), page_name)
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
    generator = None
    talk_template = None
    page_of_pages = None
    days = None
    genFactory = pagegenerators.GeneratorFactory()

    for arg in pywikibot.handleArgs(*args):
        site = pywikibot.Site()
        if arg.startswith('-talkTemplate:'):
            talk_template=arg[len("-talkTemplate:"):]
        elif arg.startswith('-pagesLinkedFrom:'):
            page_of_pages=arg[len("-pagesLinkedFrom:"):]
        elif arg.startswith('-recentchanges:'):
            days=float(arg[len("-recentchanges:"):])
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
        elif genFactory.handleArg(arg):
            # general page generators for checking the latest revision
            gen = genFactory.getCombinedGenerator()
            gen = pagegenerators.PreloadingGenerator(gen)
            generator = [(p, p.latestRevision(), 0) for p in gen]

    if (talk_template or page_of_pages or days):
        generator =  db_changes_generator(site, talk_template, page_of_pages, days, namespace)

    if generator is None:
        pywikibot.showHelp()
    else:
        bot = PlagiaBot(pywikibot.Site(), generator, report_page)
        bot.run()


if __name__ == "__main__":
    try:
        main()
    except:
        import traceback

        traceback.print_exc()
        pywikibot.stopme()
