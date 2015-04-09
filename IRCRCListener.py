"""
IRC listener to recent changes of wikis of Wikimedia foundation with similar API to that of rcstream.

(c) 2015 Eranroz

License: MIT license
"""
import pywikibot
from pywikibot.botirc import IRCBot
import threading
import sys
import re
if sys.version_info[0] > 2:
    from queue import Queue, Empty
else:
    from Queue import Queue, Empty

class IRCRecentChangesBot(IRCBot):
    def __init__(self, site, channel, nickname, server, filter_generator=None):
        super(IRCRecentChangesBot, self).__init__(site, channel, nickname, server)
        self.re_new_page_diff = re.compile('.+?index\.php\?oldid=(?P<new>[0-9]+)')
        self.re_edit_page_diff = re.compile('.+?index\.php\?diff=(?P<new>[0-9]+)&oldid=(?P<old>[0-9]+)')
        self.queue = Queue()
        if filter_generator is None:
            filter_generator = lambda x : x
        self.filter_generator  = filter_generator

    def on_pubmsg(self, c, e):
        match = self.re_edit.match(e.arguments()[0])
        if not match:
            return

        try:
            msg = e.arguments()[0].decode('utf-8')
        except UnicodeDecodeError:
            return

        name = msg[8:msg.find(u'\x0314', 9)]
        page = pywikibot.Page(self.site, name)

        is_new = 'N' in match.group('flags')
        if is_new:
            diff_match = self.re_new_page_diff.match(match.group('url'))
            if not diff_match: return
            diff_revisions = {'new': int(diff_match.group('new')), 'old': 0 }
        else:
            diff_match = self.re_edit_page_diff.match(match.group('url'))
            if not diff_match: return

            diff_revisions = { 'new': int(diff_match.group('new')), 'old': int(diff_match.group('old'))  }

        diff_data =  {
            'type': 'edit',
            'comment': match.group('summary'),
            'user': match.group('user'),
            'namespace': page.namespace(),
            'revision': diff_revisions,
            'diff_bytes': int(match.group('bytes')),
            'bot': 'B' in match.group('flags')
        }
        page._rcinfo = diff_data

        # use of generator rather than simple if allow easy use of pagegenerators
        for filtered_page in self.filter_generator([page]):
            self.queue.put(filtered_page)

class IRCRcBotThread(threading.Thread):
    def __init__(self, site, channel, nickname, server, filter_generator=None):
        super(IRCRcBotThread, self).__init__()
        self.daemon = True
        self.irc_bot = IRCRecentChangesBot(site, channel, nickname, server, filter_generator)

    def run(self):
        self.irc_bot.start()

    def stop(self):
        self.irc_bot.die()


def irc_rc_listener(site, filter_gen=None):
    channel = '#{}.{}'.format(site.lang, site.family.name)
    server = 'irc.wikimedia.org'
    nickname = site.username()
    irc_thread =  IRCRcBotThread(site, channel, nickname, server, filter_gen)
    irc_thread.start()
    while True:
        try:
            element = irc_thread.irc_bot.queue.get(timeout=0.1)
        except Empty:
            continue
        if element is None:
            return
        yield element

def main():
    print('creating site')
    site= pywikibot.Site('en')
    print('starting bot')
    for p in irc_rc_listener(site):
        print(p.title())
        print(p._rcinfo)
        print('------')


if __name__=='__main__':
    main()
