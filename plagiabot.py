import uuid
import base64
import xmlrpclib
import time

server = xmlrpclib.ServerProxy("https://api.ithenticate.com/rpc")

print "Logging in..."
login_response = server.login({"username":"...", "password": "..."})
assert(login_response['status'] == 200)
sid = login_response['sid']

print "Finding folder to upload into, with name 'Wikipedia'..."
folder_list_response = server.folder.list({'sid': sid})
assert(folder_list_response['status'] == 200)

for folder in folder_list_response['folders']:
    if folder['name'] == 'Wikipedia':
        break
else:
    raise Exception('No Wikipedia folder found!')

print "Upload test text to iThenticate..."
plagiatext = """Some boring text that I wrote myself. Definitely no plagiarism here! However, there might be some when I start talking about "ALL MY BASE ARE BELONG TO US YOU HAVE NO CHANCE TO SURVIVE MAKE YOUR TIME". That phrase or some variation of lines from the game "Zero Wing" has appeared in numerous articles, books, comics, clothing, movies, radio shows, songs, television shows, video games, webcomics, and websites."""

SUBMIT_TO_GENERATE_REPORT = 1
SUBMIT_TO_STORE_IN_REPOSITORY = 2
SUBMIT_TO_STORE_IN_REPOSITORY_AND_GENERATE_REPORT = 3

submit_response = server.document.add({
    'sid': sid,
    'submit_to': SUBMIT_TO_GENERATE_REPORT,
    'folder': folder['id'],
    'uploads': [{'title': 'Wikipedia-test ' + str(uuid.uuid4()),
                 'author_first': 'Random',
                 'author_last': 'Author',
                 'filename': '/why/do/you/care',
                 'upload': xmlrpclib.Binary(plagiatext)}]
})
assert(submit_response['status'] == 200)

upload = submit_response['uploaded'][0]

print "Polling iThenticate until document has been processed...",

while True:
    document_get_response = server.document.get({'id': upload['id'], 'sid': sid})
    assert(document_get_response['status'] == 200)
    document = document_get_response['documents'][0]
    pending = document['is_pending']
    if not pending:
        break
    print ".",
    time.sleep(5)
print ""

for part in document['parts']:
    # not sure if there is always a single part, so looping over it instead.
    print "Part #%i has a %i%% match. Getting details..." % (part['id'], part['score'])
    report_get_response = server.report.get({'id': 14557806, 'sid': sid})
    assert(report_get_response['status'] == 200)
    print "Details are available on %s" % (report_get_response['report_url'],)

    report_sources_response = server.report.sources({'id': 14557806, 'sid': sid})
    assert(report_sources_response['status'] == 200)

    print "Sources found were:"
    for source in report_sources_response['sources']:
        print " * %s % 3i%% %i words at %s" % (source['collection'][0], source['percent'], source['word_count'], source['linkurl'][:40])
