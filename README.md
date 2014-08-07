plagiabot
=========

```
valhallasw@lisilwen:~/src/plagiabot$ python -i plagiabot.py
Logging in...
Finding folder to upload into, with name 'Wikipedia'...
Upload test text to iThenticate...
Polling iThenticate until document has been processed... . . .
Part #14558041 has a 62% match. Getting details...
Details are available on https://api.ithenticate.com/report/14557806/similarity
Sources found were:
 * I  62% 42 words at http://lrd.yahooapis.com/_ylc=X3oDMTVnb2
 * I  62% 42 words at http://lrd.yahooapis.com/_ylc=X3oDMTVncn
 * I  62% 42 words at http://lrd.yahooapis.com/_ylc=X3oDMTU4aD
 * I  62% 42 words at http://www.games2.about2006.com/aboutsit
 * I  62% 42 words at http://medlibrary.org/medwiki/All_your_b
 * I  62% 42 words at http://plumbot.com/All_your_base_are_bel
 * I  62% 42 words at http://lembolies.com/Your
 * I  62% 42 words at http://dvdradix.com/capture-flash-video-
 * I  62% 42 words at http://www.dvdradix.com/capture-flash-vi
 * I  62% 42 words at http://www.reachinformation.com/define/A
 * I  60% 41 words at http://www.reference.com/browse/wiki/se/
 * I  60% 41 words at http://www.buellersdownunder.com/archive
 * I  38% 26 words at http://lrd.yahooapis.com/_ylc=X3oDMTVnbm
```


Repository for Turntin-based plagiarism detection for Wikipedia. See https://en.wikipedia.org/wiki/Wikipedia:Turnitin for details.


Useful links:
 * http://www.ithenticate.com/hs-fs/hub/92785/file-1383985272-pdf/iTh_documentation/iThenticate_API_Manual.pdf



Reporting braindump notes:
----------------------------
1. get diffs from the last day or so [on wikiproject medicine]
2. run on each diff (somehow....) which added at least N words,  N~7
3. make a daily report: a table, with columns 'plagiarism percentage', 'link to diff', 'link to details subpage'
4. make details subpage (use e.g. diffid as name) with plagiarism percentage, link to diff and list of pages that was plagiarized from. Also link to the ithenticate details page maybe.

