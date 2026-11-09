from html.parser import HTMLParser

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
SELF_CLOSING_OK = {'br','hr','img','source'}

class Node:
    __slots__ = ('tag','attrs','children','parent')
    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag; self.attrs = dict(attrs or {}); self.children = []; self.parent = parent
    def text(self):
        out=[]
        for c in self.children:
            out.append(c if isinstance(c,str) else c.text())
        return ''.join(out)

class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('#document'); self.stack=[self.root]
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.stack[-1]); self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        n = Node(tag, attrs, self.stack[-1]); self.stack[-1].children.append(n)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
    def handle_data(self, data):
        self.stack[-1].children.append(data)

def parse(html):
    b = Builder(); b.feed(html); b.close(); return b.root

def find(node, pred, acc=None):
    if acc is None: acc=[]
    if pred(node): acc.append(node)
    for c in node.children:
        if isinstance(c, Node): find(c, pred, acc)
    return acc

ESCAPE = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}

def esc(s):
    return ''.join(ESCAPE.get(ch,ch) for ch in s)
