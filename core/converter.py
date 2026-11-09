#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""html网页转换器 —— 抓取网页并生成 EPUB / DOCX / 可打印 HTML（供 App 转 PDF）。

输出协议：stdout 每行一个 JSON 对象
  {"type":"progress","pct":12,"msg":"..."}
  {"type":"result","title":"...","files":{"epub":"..."},"print_html":"...","workdir":"...","checks":[...]}
  {"type":"error","msg":"..."}
"""
import argparse, datetime, html as htmlmod, json, os, re, shutil, sys, tempfile, urllib.request, uuid, zipfile
from urllib.parse import urlparse, urljoin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dom

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
HDRS = {'User-Agent': UA, 'Referer': 'https://mp.weixin.qq.com/',
        'Accept': 'text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8'}

BLOCK = {'p', 'section', 'div', 'blockquote', 'article', 'header', 'footer', 'figure', 'figcaption'}
KEEP_INLINE = {'strong', 'b', 'em', 'i', 'u', 'sub', 'sup', 'span', 'a', 'br', 'code'}
DROP = {'script', 'style', 'mp-style-type', 'mpvoice', 'mpvideo', 'iframe', 'mp-common-profile',
        'mp-miniprogram', 'mpcps', 'noscript', 'form', 'button', 'nav', 'svg'}
NS = 'http://www.w3.org/1999/xhtml'
EMU_PER_PX = 9525
MAX_IMG_CM = 15.0


def emit(**kw):
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def progress(pct, msg):
    emit(type='progress', pct=pct, msg=msg)


def norm(s):
    return ' '.join(s.replace('\xa0', ' ').split())


def xesc(s):
    return (s.replace('\xa0', ' ').replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))


def strip_invalid(s):
    return ''.join(ch for ch in s if ch == '\n' or ch == '\t' or ord(ch) >= 32)


def fetch(url, path):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    open(path, 'wb').write(data)
    for enc in ('utf-8', 'gb18030'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'ignore')


def parse_meta(html):
    def grab(pat, default=''):
        m = re.search(pat, html)
        return m.group(1) if m else default
    title = grab(r'<meta property="og:title" content="([^"]*)"') or grab(r'<title>([^<]*)</title>')
    author = grab(r'var nickname = htmlDecode\("([^"]*)"\)') or \
             grab(r'<meta property="og:article:author" content="([^"]*)"') or \
             grab(r'<meta name="author" content="([^"]*)"')
    ts = grab(r'var ct = "(\d+)"') or grab(r'<meta property="article:published_time" content="([^"]*)"')
    date = ''
    if ts and ts.isdigit():
        date = datetime.datetime.fromtimestamp(int(ts), datetime.timezone(
            datetime.timedelta(hours=8))).strftime('%Y-%m-%d')
    elif ts:
        date = ts[:10]
    cover = grab(r'<meta property="og:image" content="([^"]*)"')
    desc = grab(r'<meta property="og:description" content="([^"]*)"')
    return {'title': htmlmod.unescape(title).strip() or '未命名网页',
            'author': htmlmod.unescape(author).strip(),
            'date': date, 'cover': cover, 'desc': htmlmod.unescape(desc).strip()}


def content_root(root):
    hit = dom.find(root, lambda n: n.tag == 'div' and n.attrs.get('id') == 'js_content')
    if hit:
        return hit[0]
    for tag in ('article', 'main'):
        hit = dom.find(root, lambda n, t=tag: n.tag == t)
        if hit:
            return hit[0]
    hit = dom.find(root, lambda n: n.tag == 'body')
    return hit[0] if hit else root


def is_bold_heading(node):
    if node.tag not in ('p', 'section', 'div', 'h1', 'h2', 'h3'):
        return False
    t = norm(node.text())
    if not t or len(t) > 60:
        return False
    if node.tag in ('h1', 'h2', 'h3'):
        return True
    bold = ''.join(b.text() for b in dom.find(node, lambda n: n.tag in ('b', 'strong')))
    return bool(bold) and norm(bold) == t


def split_chapters(content, title):
    kids = [k for k in content.children if isinstance(k, dom.Node)]
    chapters = [{'title': '', 'nodes': []}]
    for k in kids:
        if is_bold_heading(k):
            chapters.append({'title': norm(k.text()), 'nodes': [k]})
        else:
            chapters[-1]['nodes'].append(k)
    empty = {'title': '', 'nodes': []}
    if chapters and chapters[0]['title'] == '' and not chapters[0]['nodes']:
        chapters.pop(0)
    if chapters and norm(chapters[0]['title']) == norm(title):
        chapters.pop(0)
    elif chapters and not chapters[0]['title']:
        pass

    def body_len(ch):
        return sum(len(norm(n.text())) for n in ch['nodes'] if not is_bold_heading(n))

    def has_image(ch):
        for n in ch['nodes']:
            if dom.find(n, lambda x: x.tag == 'img'):
                return True
        return False

    merged, pending = [], None
    for ch in chapters:
        if ch['title'] and norm(ch['title']) == norm(title) and body_len(ch) <= 20 and not has_image(ch):
            continue
        if body_len(ch) <= 20 and not has_image(ch):
            if ch['title']:
                pending = ch['title'] if pending is None else pending + '：' + ch['title']
            continue
        if pending:
            ch['title'] = pending + '：' + ch['title']
            pending = None
        merged.append(ch)
    if pending and merged:
        merged[-1]['title'] = merged[-1]['title'] + '：' + pending
    if not merged:
        merged = [{'title': '正文', 'nodes': kids}]
    for i, ch in enumerate(merged):
        if not ch['title'].strip():
            ch['title'] = '前言' if i == 0 else '正文'
    return merged


def collect_images(content):
    urls = []
    for im in dom.find(content, lambda n: n.tag == 'img'):
        u = (im.attrs.get('data-src') or im.attrs.get('src') or '').strip()
        if not u:
            continue
        if u.startswith('//'):
            u = 'https:' + u
        if u.startswith('data:'):
            continue
        if u not in urls:
            urls.append(u)
    return urls


def img_ext(ctype, data):
    c = (ctype or '').lower()
    if 'svg' in c or data[:5] == b'<?xml' or b'<svg' in data[:400]:
        return '.svg'
    if 'png' in c or data[:4] == b'\x89PNG':
        return '.png'
    if 'gif' in c or data[:3] == b'GIF':
        return '.gif'
    if 'webp' in c or data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return '.jpg'


def download_images(urls, imgdir, skip_svg=True, progress=None):
    os.makedirs(imgdir, exist_ok=True)
    mapping, failed = {}, 0
    total = max(1, len(urls))
    for i, u in enumerate(urls):
        candidates = [re.sub(r'/\d+(\?|$)', r'/0\1', u), u] if '/mmbiz' in u else [u]
        data = None
        for cand in candidates:
            for _ in range(2):
                try:
                    req = urllib.request.Request(cand, headers=HDRS)
                    with urllib.request.urlopen(req, timeout=40) as r:
                        data = r.read()
                        ctype = r.headers.get('Content-Type', '')
                    if len(data) < 500:
                        raise ValueError('small')
                    break
                except Exception:
                    data = None
            if data:
                break
        if not data:
            failed += 1
            continue
        ext = img_ext(ctype, data)
        if skip_svg and ext == '.svg':
            continue
        fn = 'img_%03d%s' % (i + 1, ext)
        open(os.path.join(imgdir, fn), 'wb').write(data)
        mapping[u] = fn
        if progress and ((i + 1) % 5 == 0 or i + 1 == total):
            progress((i + 1) / total, '下载图片 %d/%d' % (i + 1, total))
    return mapping, failed


class Serializer(object):
    def __init__(self, mapping, prefix, img_meta=None):
        self.map = mapping
        self.prefix = prefix

    def image(self, node):
        u = (node.attrs.get('data-src') or node.attrs.get('src') or '').strip()
        if u.startswith('//'):
            u = 'https:' + u
        fn = self.map.get(u)
        if not fn:
            return ''
        alt = node.attrs.get('alt') or ''
        return '<img src="%s%s" alt="%s"/>' % (self.prefix, fn, xesc(alt))

    def ser(self, node, out):
        if isinstance(node, str):
            out.append(xesc(strip_invalid(node)))
            return
        tag = node.tag
        if tag in DROP:
            return
        if tag == 'img':
            out.append(self.image(node))
            return
        if tag == 'br':
            out.append('<br/>')
            return
        if tag == 'a':
            for c in node.children:
                self.ser(c, out)
            return
        if tag in KEEP_INLINE:
            t = 'span' if tag == 'span' else tag
            out.append('<%s>' % t)
            for c in node.children:
                self.ser(c, out)
            out.append('</%s>' % t)
            return
        if tag in BLOCK:
            buf = []
            for c in node.children:
                self.ser(c, buf)
            inner = ''.join(buf)
            if not inner.strip():
                return
            text_only = not re.sub(r'<[^>]+>', '', inner).strip()
            has_img = '<img' in inner
            has_block = re.search(r'<(p|div|h[1-6]|ul|ol|table|blockquote)(\s|>|/)', inner)
            if has_img and text_only and re.match(r'^\s*<img[^>]*/>\s*$', inner):
                out.append('<div class="img">%s</div>' % inner)
            elif has_block:
                out.append('<div class="para">%s</div>' % inner)
            else:
                out.append('<p>%s</p>' % inner)
            return
        for c in node.children:
            self.ser(c, out)


# ---------------------------------------------------------------- EPUB
EPUB_CSS = '''@charset "utf-8";
html, body { margin: 0; padding: 0; }
body { font-family: "Songti SC", "Noto Serif CJK SC", serif; line-height: 1.75;
       color: #222; padding: 0 0.7em 2em; }
h1 { font-size: 1.6em; text-align: center; margin: 1.6em 0 1em; line-height: 1.5; }
h2 { font-size: 1.35em; margin: 1.5em 0 0.8em; font-weight: bold;
     border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }
p { margin: 0.5em 0; }
div.para { margin: 0.5em 0; }
p.meta { font-size: 0.9em; color: #555; }
div.img { text-align: center; margin: 0.8em 0; }
img { max-width: 100%; height: auto; }
'''


def xhtml_page(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">\n'
            '<head><meta charset="utf-8"/><title>%s</title>\n'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
            '<body>\n%s\n</body>\n</html>\n' % (NS, xesc(title), body))


def build_epub(meta, chapters, mapping, workdir, out_path):
    root = os.path.join(workdir, 'epub')
    oebps = os.path.join(root, 'OEBPS')
    os.makedirs(os.path.join(oebps, 'text'), exist_ok=True)
    os.makedirs(os.path.join(oebps, 'images'), exist_ok=True)
    os.makedirs(os.path.join(root, 'META-INF'), exist_ok=True)

    files, used = [], set()
    ser = Serializer(mapping, '../images/')

    body = ['<h1>%s</h1>' % xesc(meta['title'])]
    if meta['author']:
        body.append('<p class="meta">%s%s</p>' % (xesc(meta['author']),
                    ('　|　' + meta['date']) if meta['date'] else ''))
    open(os.path.join(oebps, 'text', 'ch00.xhtml'), 'w', encoding='utf-8').write(
        xhtml_page(meta['title'], '<section epub:type="frontmatter">%s</section>' % ''.join(body)))
    files.append(('ch00.xhtml', '书名页'))

    for i, ch in enumerate(chapters, 1):
        buf = ['<h2>%s</h2>' % xesc(ch['title'])]
        for n in ch['nodes']:
            if is_bold_heading(n):
                continue
            ser.ser(n, buf)
        inner = '\n'.join(x for x in buf if x.strip())
        fn = 'ch%02d.xhtml' % i
        open(os.path.join(oebps, 'text', fn), 'w', encoding='utf-8').write(
            xhtml_page(ch['title'], '<section epub:type="chapter">%s</section>' % inner))
        files.append((fn, ch['title']))
        used.update(re.findall(r'\.\./images/([^"]+)"', inner))

    for f in sorted(used):
        src = os.path.join(workdir, 'images', f)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(oebps, 'images', f))

    open(os.path.join(oebps, 'style.css'), 'w', encoding='utf-8').write(EPUB_CSS)

    lis = '\n'.join('<li><a href="text/%s">%s</a></li>' % (f, xesc(t)) for f, t in files)
    open(os.path.join(oebps, 'nav.xhtml'), 'w', encoding='utf-8').write(
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="%s" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">\n'
        '<head><meta charset="utf-8"/><title>目录</title></head><body>\n'
        '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>%s</ol></nav>\n</body></html>\n' % (NS, lis))

    book_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, meta.get('url', meta['title'])))
    points = '\n'.join('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                       '<content src="text/%s"/></navPoint>' % (i, i, xesc(t), f)
                       for i, (f, t) in enumerate(files, 1))
    open(os.path.join(oebps, 'toc.ncx'), 'w', encoding='utf-8').write(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">\n'
        '<head><meta name="dtb:uid" content="urn:uuid:%s"/><meta name="dtb:depth" content="1"/>'
        '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
        '<docTitle><text>%s</text></docTitle><navMap>%s</navMap></ncx>\n'
        % (book_uuid, xesc(meta['title']), points))

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = ['<itemref idref="nav"/>']
    for f, _ in files:
        iid = re.sub(r'\W', '_', f)
        manifest.append('<item id="%s" href="text/%s" media-type="application/xhtml+xml"/>' % (iid, f))
        spine.append('<itemref idref="%s"/>' % iid)
    for f in sorted(used):
        ext = os.path.splitext(f)[1].lower()
        mt = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
              '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml'}.get(ext, 'image/jpeg')
        manifest.append('<item id="i_%s" href="images/%s" media-type="%s"/>'
                        % (re.sub(r'\W', '_', os.path.splitext(f)[0]), f, mt))

    modified = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    open(os.path.join(oebps, 'content.opf'), 'w', encoding='utf-8').write(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh-CN">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '<dc:identifier id="bookid">urn:uuid:%s</dc:identifier><dc:title>%s</dc:title>\n'
        '<dc:creator>%s</dc:creator><dc:language>zh-CN</dc:language><dc:date>%s</dc:date>\n'
        '<dc:source>%s</dc:source><dc:description>%s</dc:description>\n'
        '<meta property="dcterms:modified">%s</meta></metadata>\n'
        '<manifest>%s</manifest><spine toc="ncx">%s</spine></package>\n'
        % (book_uuid, xesc(meta['title']), xesc(meta['author']), meta['date'],
           xesc(meta.get('url', '')), xesc(meta['desc'][:300]), modified,
           '\n'.join(manifest), '\n'.join(spine)))

    open(os.path.join(root, 'mimetype'), 'w').write('application/epub+zip')
    open(os.path.join(root, 'META-INF', 'container.xml'), 'w', encoding='utf-8').write(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n')

    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, 'w') as z:
        zi = zipfile.ZipInfo('mimetype')
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, 'application/epub+zip')
        for base, _, fs in os.walk(root):
            for fn in sorted(fs):
                p = os.path.join(base, fn)
                rel = os.path.relpath(p, root)
                if rel == 'mimetype':
                    continue
                z.write(p, rel, compress_type=zipfile.ZIP_DEFLATED)
    return out_path


# ---------------------------------------------------------------- print HTML
PRINT_CSS = '''
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: "Songti SC", "STSong", "SimSun", serif; font-size: 11.5pt;
       line-height: 1.75; color: #111; }
h1 { font-size: 22pt; text-align: center; margin: 40mm 0 8mm; line-height: 1.5; }
p.meta { text-align: center; color: #555; font-size: 10pt; margin: 2mm 0; }
.url { text-align: center; color: #666; font-size: 8.5pt; word-break: break-all; }
h2 { font-size: 15pt; margin: 10mm 0 4mm; padding-bottom: 2mm; border-bottom: 0.4pt solid #bbb;
     page-break-after: avoid; }
section.chapter { page-break-before: always; }
section.front { page-break-after: always; }
p { margin: 2.4mm 0; text-align: justify; }
div.para { margin: 2.4mm 0; text-align: justify; }
div.img { text-align: center; margin: 3mm 0; page-break-inside: avoid; }
img { max-width: 100%; max-height: 310pt; }
a { color: #1a5fb4; text-decoration: none; }
'''


def build_print_pages(meta, chapters, mapping, workdir):
    """Self-contained HTML parts small enough for a reliable single WebKit PDF pass."""
    ser = Serializer(mapping, 'images/')
    paths = []
    MAX_IMGS, MAX_CHARS = 6, 2200

    def page(body, name):
        html = ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8"/>'
                '<title>%s</title><style>%s</style></head><body>%s</body></html>'
                % (xesc(meta['title']), PRINT_CSS, body))
        p = os.path.join(workdir, name)
        open(p, 'w', encoding='utf-8').write(html)
        paths.append(p)

    front = ['<section class="front"><h1>%s</h1>' % xesc(meta['title'])]
    if meta['author']:
        front.append('<p class="meta">%s%s</p>' % (xesc(meta['author']),
                      ('　|　' + meta['date']) if meta['date'] else ''))
    if meta.get('url'):
        front.append('<p class="url">%s</p>' % xesc(meta['url']))
    front.append('</section>')
    page(''.join(front), 'print_000.html')

    n = 0
    for ch in chapters:
        nodes = [x for x in ch['nodes'] if not is_bold_heading(x)]
        groups, cur, cimg, cchar = [], [], 0, 0
        for x in nodes:
            ni = len(dom.find(x, lambda y: y.tag == 'img'))
            nc = len(norm(x.text()))
            if cur and (cimg + ni > MAX_IMGS or cchar + nc > MAX_CHARS):
                groups.append(cur); cur, cimg, cchar = [], 0, 0
            cur.append(x); cimg += ni; cchar += nc
        if cur:
            groups.append(cur)
        if not groups:
            groups = [[]]
        for gi, g in enumerate(groups):
            n += 1
            title = ch['title'] if gi == 0 else ch['title'] + '（续）'
            buf = ['<h2>%s</h2>' % xesc(title)]
            for x in g:
                ser.ser(x, buf)
            page('<section class="chapter">%s</section>' % ''.join(buf), 'print_%03d.html' % n)
    return paths


# ---------------------------------------------------------------- DOCX
def img_size(data, path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.png' and data[:8] == b'\x89PNG\r\n\x1a\n':
            w = int.from_bytes(data[16:20], 'big')
            h = int.from_bytes(data[20:24], 'big')
            return w, h
        if ext == '.gif':
            return int.from_bytes(data[6:8], 'little'), int.from_bytes(data[8:10], 'little')
        if ext in ('.jpg', '.jpeg'):
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], 'big')
                    w = int.from_bytes(data[i + 7:i + 9], 'big')
                    return w, h
                if marker in (0xD8, 0xD9):
                    i += 2
                    continue
                seg = int.from_bytes(data[i + 2:i + 4], 'big')
                i += 2 + seg
    except Exception:
        pass
    return 800, 600


def w_text(s, bold=False):
    rpr = '<w:rPr><w:b/></w:rPr>' if bold else ''
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, xesc(strip_invalid(s)))


def build_docx(meta, chapters, mapping, workdir, out_path):
    media_dir = os.path.join(workdir, 'docx_media')
    os.makedirs(media_dir, exist_ok=True)
    ser = Serializer(mapping, '')
    rels, media = [], {}
    pid = [0]
    drawing_id = [0]

    def inline_runs(node, out, bold=False):
        if isinstance(node, str):
            t = strip_invalid(node)
            if t.strip():
                out.append(w_text(t, bold))
            return
        tag = node.tag
        if tag in DROP:
            return
        if tag == 'img':
            u = (node.attrs.get('data-src') or node.attrs.get('src') or '').strip()
            if u.startswith('//'):
                u = 'https:' + u
            fn = mapping.get(u)
            if not fn:
                return
            src = os.path.join(workdir, 'images', fn)
            if not os.path.exists(src):
                return
            if fn not in media:
                pid[0] += 1
                dst = 'image%d%s' % (pid[0], os.path.splitext(fn)[1])
                info = open(src, 'rb').read()
                open(os.path.join(media_dir, dst), 'wb').write(info)
                w, h = img_size(info, fn)
                max_w_cm = MAX_IMG_CM
                w_cm = w / 96.0 * 2.54
                h_cm = h / 96.0 * 2.54
                if w_cm > max_w_cm:
                    h_cm = h_cm * max_w_cm / w_cm
                    w_cm = max_w_cm
                media[fn] = (dst, int(w_cm * 360000), int(h_cm * 360000), w, h)
                rels.append('<Relationship Id="rIdImg%d" Type="http://schemas.openxmlformats.org/'
                            'officeDocument/2006/relationships/image" Target="media/%s"/>' % (pid[0], dst))
            dst, cx, cy, w, h = media[fn]
            drawing_id[0] += 1
            out.append(
                '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                '<wp:extent cx="%d" cy="%d"/><wp:docPr id="%d" name="Picture %d"/>'
                '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                '<pic:pic><pic:nvPicPr><pic:cNvPr id="%d" name="%s"/><pic:cNvPicPr/></pic:nvPicPr>'
                '<pic:blipFill><a:blip r:embed="rIdImg%d"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
                % (cx, cy, drawing_id[0], drawing_id[0], drawing_id[0], xesc(dst),
                   pid[0], cx, cy))
            return
        if tag == 'br':
            out.append('<w:r><w:br/></w:r>')
            return
        if tag == 'a':
            for c in node.children:
                inline_runs(c, out, bold)
            return
        if tag in ('b', 'strong'):
            for c in node.children:
                inline_runs(c, out, True)
            return
        if tag in KEEP_INLINE:
            for c in node.children:
                inline_runs(c, out, bold)
            return
        if tag in BLOCK:
            for c in node.children:
                inline_runs(c, out, bold)
            return
        for c in node.children:
            inline_runs(c, out, bold)

    def para(node, style=None, center=False):
        buf = []
        inline_runs(node, buf)
        inner = ''.join(buf)
        if not inner.strip():
            return ''
        ppr = ''
        if style or center:
            ppr = '<w:pPr>'
            if style:
                ppr += '<w:pStyle w:val="%s"/>' % style
            if center:
                ppr += '<w:jc w:val="center"/>'
            ppr += '</w:pPr>'
        return '<w:p>%s%s</w:p>' % (ppr, inner)

    body = []
    body.append('<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>%s</w:p>' % w_text(meta['title'], True))
    if meta['author']:
        body.append('<w:p><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr>%s</w:p>'
                    % w_text('%s%s' % (meta['author'], ('　|　' + meta['date']) if meta['date'] else '')))
    if meta.get('url'):
        body.append('<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr>%s</w:p>' % w_text(meta['url']))

    for ch in chapters:
        body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>%s</w:p>' % w_text(ch['title']))
        for n in ch['nodes']:
            if is_bold_heading(n):
                continue
            if isinstance(n, str):
                if n.strip():
                    body.append('<w:p>%s</w:p>' % w_text(n))
                continue
            # image-only block → centred paragraph with picture
            imgs = dom.find(n, lambda x: x.tag == 'img')
            if imgs and not re.sub(r'\s', '', re.sub(r'<[^>]+>', '', ''.join(
                    '' if isinstance(c, dom.Node) else c for c in n.children))):
                buf = []
                inline_runs(n, buf)
                inner = ''.join(buf)
                if inner.strip():
                    body.append('<w:p><w:pPr><w:jc w:val="center"/></w:pPr>%s</w:p>' % inner)
                continue
            p = para(n)
            if p:
                body.append(p)

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
            'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
           'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
           'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
           '<w:body>%s%s</w:body></w:document>' % (''.join(body), sect))

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/>
<w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="330" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:jc w:val="center"/><w:spacing w:before="2400" w:after="600"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="44"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/>
<w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:color w:val="555555"/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="160"/>
<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="Caption"/><w:basedOn w:val="Normal"/>
<w:rPr><w:color w:val="666666"/><w:sz w:val="18"/></w:rPr></w:style>
</w:styles>'''

    ct = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="jpg" ContentType="image/jpeg"/><Default Extension="jpeg" ContentType="image/jpeg"/>
<Default Extension="png" ContentType="image/png"/><Default Extension="gif" ContentType="image/gif"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

    rels_doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '%s</Relationships>' % ''.join(rels))
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 '</Relationships>')

    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('word/document.xml', doc)
        z.writestr('word/styles.xml', styles)
        z.writestr('word/_rels/document.xml.rels', rels_doc)
        for fn in sorted(os.listdir(media_dir)):
            z.write(os.path.join(media_dir, fn), 'word/media/' + fn)
    return out_path


# ---------------------------------------------------------------- validation
def validate_epub(path):
    import xml.etree.ElementTree as ET
    issues = []
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return ['无法打开 EPUB：%s' % e]
    names = z.namelist()
    if not names or names[0] != 'mimetype':
        issues.append('mimetype 不是第一个条目')
    else:
        if z.getinfo('mimetype').compress_type != zipfile.ZIP_STORED:
            issues.append('mimetype 被压缩')
        if z.read('mimetype') != b'application/epub+zip':
            issues.append('mimetype 内容不正确')
    for n in names:
        if n.endswith(('.xhtml', '.opf', '.ncx', '.xml')):
            try:
                ET.fromstring(z.read(n))
            except Exception as e:
                issues.append('%s XML 解析失败：%s' % (n, e))
    opf = z.read('OEBPS/content.opf').decode('utf-8')
    for href in re.findall(r'href="([^"]+)"', opf):
        target = 'OEBPS/' + href
        if href.startswith('http') or href.startswith('#'):
            continue
        if target not in names:
            issues.append('清单文件缺失：%s' % href)
    for n in names:
        if n.endswith('.xhtml'):
            for src in re.findall(r'<img[^>]+src="([^"]+)"', z.read(n).decode('utf-8')):
                full = os.path.normpath(os.path.join(os.path.dirname(n), src))
                if full not in names:
                    issues.append('图片缺失：%s' % src)
    return issues


def validate_docx(path):
    import xml.etree.ElementTree as ET
    issues = []
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return ['无法打开 DOCX：%s' % e]
    for part in ('[Content_Types].xml', '_rels/.rels', 'word/document.xml'):
        if part not in z.namelist():
            issues.append('缺少部件：%s' % part)
            continue
        try:
            ET.fromstring(z.read(part))
        except Exception as e:
            issues.append('%s XML 解析失败：%s' % (part, e))
    if 'word/_rels/document.xml.rels' in z.namelist():
        try:
            ET.fromstring(z.read('word/_rels/document.xml.rels'))
        except Exception as e:
            issues.append('关系文件解析失败：%s' % e)
    return issues


# ---------------------------------------------------------------- PDF (reportlab, portable)
def _inline_markup(node, bold=False, out=None):
    if out is None:
        out = []
    if isinstance(node, str):
        t = strip_invalid(node)
        if t:
            esc = xesc(t).replace('\n', ' ')
            out.append('<b>%s</b>' % esc if bold else esc)
        return out
    tag = node.tag
    if tag in DROP:
        return out
    if tag == 'img':
        return out
    if tag == 'br':
        out.append('<br/>')
        return out
    if tag in ('b', 'strong'):
        for c in node.children:
            _inline_markup(c, True, out)
        return out
    if tag == 'a':
        for c in node.children:
            _inline_markup(c, bold, out)
        return out
    for c in node.children:
        _inline_markup(c, bold, out)
    return out


def _flow_items(node, mapping, out):
    if isinstance(node, str):
        if node.strip():
            out.append(('text', xesc(node.strip())))
        return
    tag = node.tag
    if tag in DROP:
        return
    if tag == 'img':
        u = (node.attrs.get('data-src') or node.attrs.get('src') or '').strip()
        if u.startswith('//'):
            u = 'https:' + u
        if mapping.get(u):
            out.append(('img', mapping[u]))
        return
    if tag == 'br':
        return
    if tag in BLOCK:
        has_block = dom.find(node, lambda n: n.tag in BLOCK or n.tag == 'img')
        if has_block:
            for c in node.children:
                _flow_items(c, mapping, out)
        else:
            markup = ''.join(_inline_markup(node)).strip()
            if markup:
                out.append(('text', markup))
        return
    markup = ''.join(_inline_markup(node)).strip()
    if markup:
        out.append(('text', markup))


def build_pdf_reportlab(meta, chapters, mapping, workdir, out_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                    Spacer, Image, PageBreak, KeepTogether)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    F = 'STSong-Light'
    st_title = ParagraphStyle('t', fontName=F, fontSize=22, leading=32, alignment=TA_CENTER)
    st_meta = ParagraphStyle('m', fontName=F, fontSize=9, leading=14, alignment=TA_CENTER,
                             textColor=colors.HexColor('#555555'))
    st_h2 = ParagraphStyle('h2', fontName=F, fontSize=15, leading=22, spaceBefore=10, spaceAfter=8,
                           textColor=colors.HexColor('#111111'))
    st_body = ParagraphStyle('b', fontName=F, fontSize=10.5, leading=16.5, alignment=TA_JUSTIFY,
                             spaceAfter=4)
    st_cap = ParagraphStyle('c', fontName=F, fontSize=9, leading=14, textColor=colors.HexColor('#444444'),
                            spaceAfter=6)

    W, H = A4
    LM = RM = 16 * mm
    TM = 18 * mm
    BM = 18 * mm
    content_w = W - LM - RM

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(F, 8)
        canvas.setFillColor(colors.HexColor('#888888'))
        canvas.drawCentredString(W / 2, 10 * mm, '%d' % doc.page)
        canvas.restoreState()

    doc = BaseDocTemplate(out_path, pagesize=A4, leftMargin=LM, rightMargin=RM,
                          topMargin=TM, bottomMargin=BM, title=meta['title'],
                          author=meta.get('author', ''), subject=meta.get('url', ''))
    frame = Frame(LM, BM, content_w, H - TM - BM, id='f', showBoundary=0)
    doc.addPageTemplates([PageTemplate(id='p', frames=[frame], onPage=footer)])

    story = [Spacer(1, 55 * mm), Paragraph(xesc(meta['title']), st_title), Spacer(1, 8 * mm)]
    if meta.get('author'):
        story.append(Paragraph('%s%s' % (xesc(meta['author']),
                              ('　|　' + meta['date']) if meta.get('date') else ''), st_meta))
    if meta.get('url'):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(xesc(meta['url']), st_meta))
    story.append(PageBreak())

    def img_flow(fn):
        path = os.path.join(workdir, 'images', fn)
        try:
            iw, ih = img_size(open(path, 'rb').read(), fn)
        except Exception:
            return None
        if not iw or not ih:
            return None
        w = content_w
        h = w * ih / float(iw)
        max_h = 300
        if h > max_h:
            h = max_h
            w = h * iw / float(ih)
        return Image(path, width=w, height=h)

    for ch in chapters:
        items = []
        for n in ch['nodes']:
            if is_bold_heading(n):
                continue
            _flow_items(n, mapping, items)
        if not items:
            continue
        story.append(Paragraph(xesc(ch['title']), st_h2))
        for kind, val in items:
            if kind == 'text':
                try:
                    story.append(Paragraph(val, st_body))
                except Exception:
                    story.append(Paragraph(xesc(re.sub(r'<[^>]+>', '', val)), st_body))
            else:
                fl = img_flow(val)
                if fl is not None:
                    story.append(KeepTogether([Spacer(1, 2 * mm), fl, Spacer(1, 2 * mm)]))
    doc.build(story)
    return out_path


# ---------------------------------------------------------------- pipeline
def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '-', s).strip()
    return s[:80] or '网页导出'


def convert(url, outdir, formats, progress_cb=None, pdf_engine='native'):
    """Run the whole pipeline. Returns a result dict; raises on fatal errors."""
    say = progress_cb or (lambda pct, msg: None)
    outdir = os.path.expanduser(outdir)
    os.makedirs(outdir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='webtobook-')
    try:
        say(3, '正在抓取网页…')
        html = fetch(url, os.path.join(workdir, 'article.html'))
        meta = parse_meta(html)
        meta['url'] = url

        say(8, '正在解析正文…')
        root = dom.parse(html)
        content = content_root(root)
        chapters = split_chapters(content, meta['title'])
        urls = collect_images(content)
        say(10, '发现 %d 个章节、%d 张图片' % (len(chapters), len(urls)))

        mapping, failed = download_images(urls, os.path.join(workdir, 'images'),
                                          progress=lambda p, m: say(10 + int(38 * p), m))
        say(48, '图片处理完成（成功 %d，失败 %d）' % (len(mapping), failed))

        base = safe_name(meta['title'])
        files, checks, print_pages = {}, [], []
        if 'epub' in formats:
            say(52, '正在生成 EPUB…')
            p = os.path.join(outdir, base + '.epub')
            build_epub(meta, chapters, mapping, workdir, p)
            issues = validate_epub(p)
            checks.append({'fmt': 'EPUB', 'ok': not issues, 'issues': issues[:5]})
            files['epub'] = p

        if 'pdf' in formats:
            say(66, '正在排版 PDF…')
            if pdf_engine == 'reportlab':
                p = os.path.join(outdir, base + '.pdf')
                build_pdf_reportlab(meta, chapters, mapping, workdir, p)
                checks.append({'fmt': 'PDF', 'ok': True, 'issues': []})
                files['pdf'] = p
            else:
                print_pages = build_print_pages(meta, chapters, mapping, workdir)
                checks.append({'fmt': 'PDF', 'ok': True, 'issues': ['等待原生渲染']})

        if 'docx' in formats:
            say(80, '正在生成 DOCX…')
            p = os.path.join(outdir, base + '.docx')
            build_docx(meta, chapters, mapping, workdir, p)
            issues = validate_docx(p)
            checks.append({'fmt': 'DOCX', 'ok': not issues, 'issues': issues[:5]})
            files['docx'] = p

        say(92, '完成')
        return {'type': 'result', 'title': meta['title'], 'author': meta['author'],
                'date': meta['date'], 'files': files, 'print_pages': print_pages,
                'workdir': workdir, 'chapters': len(chapters), 'images': len(mapping),
                'failed_images': failed, 'checks': checks}
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--formats', default='epub,pdf,docx')
    ap.add_argument('--pdf-engine', default='native', choices=['native', 'reportlab'])
    ap.add_argument('--workdir', default='')
    args = ap.parse_args()

    formats = [f.strip().lower() for f in args.formats.split(',') if f.strip()]

    def progress(pct, msg):
        emit(type='progress', pct=pct, msg=msg)

    try:
        res = convert(args.url, args.outdir, formats, progress_cb=progress,
                      pdf_engine=args.pdf_engine)
        if args.workdir:
            res['workdir'] = args.workdir
        emit(**res)
    except Exception as e:
        import traceback
        emit(type='error', msg=str(e), detail=traceback.format_exc()[-1500:])
        sys.exit(1)


if __name__ == '__main__':
    main()
