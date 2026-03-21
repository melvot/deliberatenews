"""Display cluster labels and headlines from the latest generated digest."""

import sys
from html.parser import HTMLParser


class DigestParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tag = None
        self.txt = ""
        self.clusters = []

    def handle_starttag(self, t, a):
        if t in ("h2", "a"):
            self.tag = t
            self.txt = ""

    def handle_data(self, d):
        if self.tag:
            self.txt += d

    def handle_endtag(self, t):
        if t == "h2" and self.tag == "h2":
            self.clusters.append((self.txt, []))
        elif t == "a" and self.tag == "a":
            if self.clusters:
                self.clusters[-1][1].append(self.txt)
        if t == self.tag:
            self.tag = None


path = sys.argv[1] if len(sys.argv) > 1 else "docs/index.html"
parser = DigestParser()
with open(path) as f:
    parser.feed(f.read())

total = 0
for name, stories in parser.clusters:
    print(f"\n## {name} ({len(stories)})")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. {s}")
    total += len(stories)

print(f"\nTotal: {total} stories in {len(parser.clusters)} clusters")
