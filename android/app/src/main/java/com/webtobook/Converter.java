package com.webtobook;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;

import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** Fetches a web article and builds EPUB / PDF / DOCX files. */
public class Converter {

    public interface Progress { void onProgress(int pct, String msg); }

    public static class Result {
        public String title;
        public String author;
        public String date;
        public List<String> savedNames = new ArrayList<>();
        public int chapters;
        public int images;
    }

    private static final String UA =
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36";
    private static final String REFERER = "https://mp.weixin.qq.com/";

    static class Chapter {
        String title;
        List<Element> nodes = new ArrayList<>();
        Chapter(String t) { title = t; }
    }

    public static Result convert(Context ctx, String url, boolean doEpub, boolean doPdf, boolean doDocx,
                                 Progress cb) throws Exception {
        Result res = new Result();
        File work = new File(ctx.getCacheDir(), "w2b");
        deleteRec(work);
        work.mkdirs();
        File imgDir = new File(work, "images");
        imgDir.mkdirs();

        cb.onProgress(3, "正在抓取网页…");
        String html = fetch(url);

        cb.onProgress(8, "正在解析正文…");
        Document doc = Jsoup.parse(html);
        res.title = meta(doc, html, "og:title");
        if (res.title.isEmpty()) res.title = doc.title();
        if (res.title.isEmpty()) res.title = "网页导出";
        res.author = first(html, "var nickname = htmlDecode\\(\"([^\"]*)\"\\)");
        String ct = first(html, "var ct = \"(\\d+)\"");
        if (!ct.isEmpty()) {
            res.date = new SimpleDateFormat("yyyy-MM-dd", Locale.CHINA)
                    .format(new Date(Long.parseLong(ct) * 1000L));
        }

        Element content = doc.selectFirst("#js_content");
        if (content == null) content = doc.selectFirst("article");
        if (content == null) content = doc.body();

        List<Chapter> chapters = split(content);
        res.chapters = chapters.size();

        Elements imgs = content.select("img");
        Map<String, File> map = new LinkedHashMap<>();
        int idx = 0;
        for (Element im : imgs) {
            String u = im.hasAttr("data-src") ? im.attr("data-src") : im.attr("src");
            if (u == null) u = "";
            u = u.trim();
            if (u.isEmpty() || u.contains("mmbiz_svg") || u.startsWith("data:")) continue;
            if (u.startsWith("//")) u = "https:" + u;
            if (map.containsKey(u)) continue;
            try {
                byte[] data = fetchBytes(u);
                if (data == null || data.length < 500) continue;
                String ext = guessExt(data);
                File f = new File(imgDir, String.format(Locale.US, "img_%03d%s", idx++, ext));
                writeFile(f, data);
                map.put(u, f);
                cb.onProgress(10 + (int) (35.0 * idx / Math.max(1, imgs.size())),
                        "下载图片 " + idx + "/" + imgs.size());
            } catch (Exception ignored) { }
        }
        res.images = map.size();
        cb.onProgress(48, "图片处理完成（" + map.size() + " 张）");

        String base = safe(res.title);
        if (doEpub) {
            cb.onProgress(55, "正在生成 EPUB…");
            File f = new File(work, base + ".epub");
            buildEpub(res, chapters, map, f);
            res.savedNames.add(saveToDownloads(ctx, f, base + ".epub"));
        }
        if (doPdf) {
            cb.onProgress(70, "正在排版 PDF…");
            File f = new File(work, base + ".pdf");
            buildPdf(res, chapters, map, f);
            res.savedNames.add(saveToDownloads(ctx, f, base + ".pdf"));
        }
        if (doDocx) {
            cb.onProgress(85, "正在生成 DOCX…");
            File f = new File(work, base + ".docx");
            buildDocx(res, chapters, map, f);
            res.savedNames.add(saveToDownloads(ctx, f, base + ".docx"));
        }
        cb.onProgress(98, "完成");
        return res;
    }

    // ------------------------------------------------------------ network
    static String fetch(String url) throws IOException {
        byte[] b = fetchBytes(url);
        return new String(b, StandardCharsets.UTF_8);
    }

    static byte[] fetchBytes(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(20000);
        c.setReadTimeout(40000);
        c.setInstanceFollowRedirects(true);
        c.setRequestProperty("User-Agent", UA);
        c.setRequestProperty("Referer", REFERER);
        c.setRequestProperty("Accept", "text/html,image/*,*/*;q=0.8");
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            return bos.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    // ------------------------------------------------------------ parsing
    static String meta(Document doc, String html, String prop) {
        Element m = doc.selectFirst("meta[property=" + prop + "]");
        return m == null ? "" : m.attr("content").trim();
    }

    static String first(String s, String re) {
        Matcher m = Pattern.compile(re).matcher(s);
        return m.find() ? m.group(1) : "";
    }

    static boolean isHeading(Element el) {
        String tag = el.tagName();
        if (!(tag.equals("p") || tag.equals("section") || tag.equals("div")
                || tag.equals("h1") || tag.equals("h2") || tag.equals("h3"))) return false;
        String text = el.text().trim();
        if (text.isEmpty() || text.length() > 60) return false;
        if (tag.startsWith("h")) return true;
        StringBuilder bold = new StringBuilder();
        for (Element b : el.select("b,strong")) bold.append(b.text());
        return bold.length() > 0 && bold.toString().trim().equals(text);
    }

    static List<Chapter> split(Element content) {
        List<Chapter> list = new ArrayList<>();
        Chapter cur = new Chapter("");
        list.add(cur);
        for (Element el : content.children()) {
            if (isHeading(el)) {
                cur = new Chapter(el.text().trim());
                list.add(cur);
            } else {
                cur.nodes.add(el);
            }
        }
        if (!list.isEmpty() && list.get(0).title.isEmpty() && list.get(0).nodes.isEmpty()) list.remove(0);
        if (list.size() > 1 && !list.get(0).title.isEmpty()
                && (list.get(0).nodes.isEmpty())) list.remove(0);
        // merge chapters with almost no content into the next one
        List<Chapter> merged = new ArrayList<>();
        String pending = null;
        for (Chapter ch : list) {
            int chars = 0, imgs = 0;
            for (Element e : ch.nodes) {
                chars += e.text().trim().length();
                imgs += e.select("img").size();
            }
            if (chars <= 20 && imgs == 0) {
                if (!ch.title.isEmpty()) pending = pending == null ? ch.title : pending + "：" + ch.title;
                continue;
            }
            if (pending != null) { ch.title = pending + "：" + ch.title; pending = null; }
            merged.add(ch);
        }
        if (pending != null && !merged.isEmpty()) merged.get(merged.size() - 1).title += "：" + pending;
        for (int i = 0; i < merged.size(); i++) {
            if (merged.get(i).title.isEmpty()) merged.get(i).title = i == 0 ? "前言" : "正文";
        }
        if (merged.isEmpty()) {
            Chapter c = new Chapter("正文");
            c.nodes.addAll(content.children());
            merged.add(c);
        }
        return merged;
    }

    // ------------------------------------------------------------ epub
    static void buildEpub(Result res, List<Chapter> chapters, Map<String, File> map, File out) throws Exception {
        try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(out))) {
            ZipEntry mt = new ZipEntry("mimetype");
            byte[] mtData = "application/epub+zip".getBytes(StandardCharsets.UTF_8);
            mt.setMethod(ZipEntry.STORED);
            mt.setSize(mtData.length);
            CRC32 crc = new CRC32();
            crc.update(mtData);
            mt.setCrc(crc.getValue());
            zos.putNextEntry(mt);
            zos.write(mtData);
            zos.closeEntry();

            put(zos, "META-INF/container.xml",
                    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
                    "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">" +
                    "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>" +
                    "</rootfiles></container>");

            StringBuilder manifest = new StringBuilder();
            StringBuilder spine = new StringBuilder();
            StringBuilder nav = new StringBuilder();

            put(zos, "OEBPS/style.css",
                    "body{font-family:serif;line-height:1.7;padding:0 1em}h1{text-align:center;font-size:1.6em}" +
                    "h2{font-size:1.3em;border-bottom:1px solid #ddd;padding-bottom:.3em;margin-top:1.4em}" +
                    "p{margin:.5em 0}img{max-width:100%;height:auto;display:block;margin:.6em auto}");

            int n = 0;
            StringBuilder chXhtml = new StringBuilder();
            for (Chapter ch : chapters) {
                n++;
                String fn = "ch" + String.format(Locale.US, "%02d", n) + ".xhtml";
                StringBuilder body = new StringBuilder();
                body.append("<h2>").append(esc(ch.title)).append("</h2>");
                for (Element el : ch.nodes) body.append(renderBlock(el, map));
                put(zos, "OEBPS/text/" + fn, xhtml(ch.title, body.toString()));
                manifest.append("<item id=\"c").append(n).append("\" href=\"text/").append(fn)
                        .append("\" media-type=\"application/xhtml+xml\"/>");
                spine.append("<itemref idref=\"c").append(n).append("\"/>");
                nav.append("<li><a href=\"text/").append(fn).append("\">").append(esc(ch.title)).append("</a></li>");
            }

            put(zos, "OEBPS/nav.xhtml",
                    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<!DOCTYPE html>\n" +
                    "<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" " +
                    "xml:lang=\"zh-CN\" lang=\"zh-CN\"><head><meta charset=\"utf-8\"/><title>目录</title>" +
                    "<link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"/></head><body>" +
                    "<nav epub:type=\"toc\" id=\"toc\"><h1>目录</h1><ol>" + nav + "</ol></nav></body></html>");

            for (Map.Entry<String, File> e : map.entrySet()) {
                String nm = e.getValue().getName();
                putBytes(zos, "OEBPS/images/" + nm, readFile(e.getValue()));
                String mt2 = nm.endsWith(".png") ? "image/png" : nm.endsWith(".gif") ? "image/gif" : "image/jpeg";
                manifest.append("<item id=\"i_").append(nm.replace(".", "_")).append("\" href=\"images/")
                        .append(nm).append("\" media-type=\"").append(mt2).append("\"/>");
            }

            String opf = "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
                    "<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"3.0\" unique-identifier=\"id\" xml:lang=\"zh-CN\">" +
                    "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">" +
                    "<dc:identifier id=\"id\">urn:uuid:" + java.util.UUID.nameUUIDFromBytes(
                            (res.title + res.date).getBytes(StandardCharsets.UTF_8)) + "</dc:identifier>" +
                    "<dc:title>" + esc(res.title) + "</dc:title>" +
                    "<dc:creator>" + esc(res.author) + "</dc:creator>" +
                    "<dc:language>zh-CN</dc:language><dc:date>" + res.date + "</dc:date>" +
                    "<meta property=\"dcterms:modified\">2026-01-01T00:00:00Z</meta></metadata>" +
                    "<manifest><item id=\"nav\" href=\"nav.xhtml\" media-type=\"application/xhtml+xml\" properties=\"nav\"/>" +
                    "<item id=\"css\" href=\"style.css\" media-type=\"text/css\"/>" + manifest + "</manifest>" +
                    "<spine>" + spine + "</spine></package>";
            put(zos, "OEBPS/content.opf", opf);
        }
    }

    static String xhtml(String title, String body) {
        return "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<!DOCTYPE html>\n" +
                "<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" " +
                "xml:lang=\"zh-CN\" lang=\"zh-CN\"><head><meta charset=\"utf-8\"/><title>" + esc(title) +
                "</title><link rel=\"stylesheet\" type=\"text/css\" href=\"../style.css\"/></head><body>" +
                body + "</body></html>";
    }

    static String renderBlock(Element el, Map<String, File> map) {
        Elements imgs = el.select("img");
        if (imgs.size() > 0 && el.text().trim().isEmpty()) {
            StringBuilder sb = new StringBuilder("<div>");
            for (Element im : imgs) {
                String u = im.hasAttr("data-src") ? im.attr("data-src") : im.attr("src");
                if (u == null) continue;
                u = u.trim();
                if (u.startsWith("//")) u = "https:" + u;
                File f = map.get(u);
                if (f != null) sb.append("<img src=\"../images/").append(f.getName()).append("\" alt=\"\"/>");
            }
            return sb.append("</div>").toString();
        }
        String t = el.text().trim();
        if (t.isEmpty()) return "";
        return "<p>" + esc(t) + "</p>";
    }

    // ------------------------------------------------------------ pdf
    static class PdfWriter {
        final PdfDocument doc = new PdfDocument();
        final int W = 595, H = 842, M = 40;
        PdfDocument.Page page;
        Canvas canvas;
        float y = M;
        int no = 0;

        PdfWriter() { newPage(); }

        void newPage() {
            if (page != null) {
                doc.finishPage(page);
            }
            no++;
            page = doc.startPage(new PdfDocument.PageInfo.Builder(W, H, no).create());
            canvas = page.getCanvas();
            y = M;
        }

        void ensure(float h) { if (y + h > H - M) newPage(); }

        void save(File out) throws IOException {
            if (page != null) { doc.finishPage(page); page = null; }
            try (FileOutputStream fos = new FileOutputStream(out)) { doc.writeTo(fos); }
            doc.close();
        }
    }

    static void buildPdf(Result res, List<Chapter> chapters, Map<String, File> map, File out) throws Exception {
        PdfWriter w = new PdfWriter();
        Paint tp = new Paint(Paint.ANTI_ALIAS_FLAG);
        tp.setColor(Color.BLACK);
        tp.setTextAlign(Paint.Align.CENTER);

        tp.setTypeface(Typeface.create(Typeface.SERIF, Typeface.BOLD));
        tp.setTextSize(24f);
        w.canvas.drawText(res.title, w.W / 2f, w.H * 0.40f, tp);
        tp.setTextSize(11f);
        tp.setTypeface(Typeface.create(Typeface.SERIF, Typeface.NORMAL));
        if (!res.author.isEmpty()) {
            String sub = res.author + (res.date.isEmpty() ? "" : "  |  " + res.date);
            w.canvas.drawText(sub, w.W / 2f, w.H * 0.40f + 42, tp);
        }
        w.newPage();

        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.BLACK);
        Typeface body = Typeface.create(Typeface.SERIF, Typeface.NORMAL);
        Typeface head = Typeface.create(Typeface.SERIF, Typeface.BOLD);

        for (Chapter ch : chapters) {
            TextPaint hp = new TextPaint(paint);
            hp.setTypeface(head);
            hp.setTextSize(16f);
            StaticLayout hl = StaticLayout.Builder.obtain(ch.title, 0, ch.title.length(), hp, w.W - 2 * w.M).build();
            w.ensure(hl.getHeight() + 30);
            w.canvas.save();
            w.canvas.translate(w.M, w.y);
            hl.draw(w.canvas);
            w.canvas.restore();
            w.y += hl.getHeight() + 10;

            for (Element el : ch.nodes) {
                Elements imgs = el.select("img");
                if (imgs.size() > 0) {
                    for (Element im : imgs) {
                        String u = im.hasAttr("data-src") ? im.attr("data-src") : im.attr("src");
                        if (u == null) continue;
                        u = u.trim();
                        if (u.startsWith("//")) u = "https:" + u;
                        File f = map.get(u);
                        if (f == null) continue;
                        Bitmap bm = BitmapFactory.decodeFile(f.getAbsolutePath());
                        if (bm == null) continue;
                        float maxW = w.W - 2 * w.M, maxH = 300;
                        float scale = Math.min(maxW / bm.getWidth(), maxH / bm.getHeight());
                        float dw = bm.getWidth() * scale, dh = bm.getHeight() * scale;
                        w.ensure(dh + 12);
                        float left = (w.W - dw) / 2f;
                        w.canvas.drawBitmap(bm, null,
                                new android.graphics.RectF(left, w.y, left + dw, w.y + dh), paint);
                        w.y += dh + 10;
                        bm.recycle();
                    }
                    continue;
                }
                String text = el.text().trim();
                if (text.isEmpty()) continue;
                TextPaint tpx = new TextPaint(paint);
                tpx.setTypeface(body);
                tpx.setTextSize(11f);
                StaticLayout sl = StaticLayout.Builder.obtain(text, 0, text.length(), tpx, w.W - 2 * w.M)
                        .setLineSpacing(4f, 1f)
                        .build();
                w.ensure(sl.getHeight() + 6);
                w.canvas.save();
                w.canvas.translate(w.M, w.y);
                sl.draw(w.canvas);
                w.canvas.restore();
                w.y += sl.getHeight() + 6;
            }
        }
        w.save(out);
    }

    // ------------------------------------------------------------ docx
    static void buildDocx(Result res, List<Chapter> chapters, Map<String, File> map, File out) throws Exception {
        try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(out))) {
            put(zos, "[Content_Types].xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
                    "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" +
                    "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>" +
                    "<Default Extension=\"xml\" ContentType=\"application/xml\"/>" +
                    "<Default Extension=\"jpg\" ContentType=\"image/jpeg\"/>" +
                    "<Default Extension=\"png\" ContentType=\"image/png\"/>" +
                    "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>" +
                    "</Types>");
            put(zos, "_rels/.rels",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
                    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
                    "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>" +
                    "</Relationships>");

            StringBuilder body = new StringBuilder();
            body.append(paragraph(res.title, true, 36));
            if (!res.author.isEmpty()) body.append(paragraph(res.author + "  " + res.date, false, 20));

            StringBuilder rels = new StringBuilder();
            int rid = 100;
            int picId = 1;
            for (Chapter ch : chapters) {
                body.append(paragraph(ch.title, true, 28));
                for (Element el : ch.nodes) {
                    Elements imgs = el.select("img");
                    if (imgs.size() > 0 && el.text().trim().isEmpty()) {
                        for (Element im : imgs) {
                            String u = im.hasAttr("data-src") ? im.attr("data-src") : im.attr("src");
                            if (u == null) continue;
                            u = u.trim();
                            if (u.startsWith("//")) u = "https:" + u;
                            File f = map.get(u);
                            if (f == null) continue;
                            String nm = f.getName();
                            putBytes(zos, "word/media/" + nm, readFile(f));
                            rels.append("<Relationship Id=\"rId").append(rid)
                                    .append("\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image\" Target=\"media/")
                                    .append(nm).append("\"/>");
                            int[] wh = imageSize(f);
                            int cx = wh[0] * 9525, cy = wh[1] * 9525;
                            body.append("<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr><w:r><w:drawing>" +
                                    "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">" +
                                    "<wp:extent cx=\"" + cx + "\" cy=\"" + cy + "\"/>" +
                                    "<wp:docPr id=\"" + picId + "\" name=\"Picture " + picId + "\"/>" +
                                    "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">" +
                                    "<pic:pic><pic:nvPicPr><pic:cNvPr id=\"" + picId + "\" name=\"" + nm + "\"/><pic:cNvPicPr/></pic:nvPicPr>" +
                                    "<pic:blipFill><a:blip r:embed=\"rId" + rid + "\"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>" +
                                    "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"" + cx + "\" cy=\"" + cy + "\"/></a:xfrm>" +
                                    "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>" +
                                    "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>");
                            rid++;
                            picId++;
                        }
                        continue;
                    }
                    String t = el.text().trim();
                    if (!t.isEmpty()) body.append(paragraph(t, false, 22));
                }
            }

            String doc = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
                    "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" " +
                    "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" " +
                    "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" " +
                    "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" " +
                    "xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">" +
                    "<w:body>" + body + "</w:body></w:document>";
            put(zos, "word/document.xml", doc);
            put(zos, "word/_rels/document.xml.rels",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
                    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
                    rels + "</Relationships>");
        }
    }

    static String paragraph(String text, boolean bold, int halfPoints) {
        return "<w:p><w:r><w:rPr>" + (bold ? "<w:b/>" : "") + "<w:sz w:val=\"" + halfPoints + "\"/></w:rPr>" +
                "<w:t xml:space=\"preserve\">" + esc(text) + "</w:t></w:r></w:p>";
    }

    static int[] imageSize(File f) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getAbsolutePath(), o);
        int w = o.outWidth > 0 ? o.outWidth : 800;
        int h = o.outHeight > 0 ? o.outHeight : 600;
        double maxW = 15.0 * 96 / 2.54;
        if (w > maxW) { h = (int) (h * maxW / w); w = (int) maxW; }
        return new int[]{w, h};
    }

    // ------------------------------------------------------------ io helpers
    static void put(ZipOutputStream zos, String name, String content) throws IOException {
        putBytes(zos, name, content.getBytes(StandardCharsets.UTF_8));
    }

    static void putBytes(ZipOutputStream zos, String name, byte[] data) throws IOException {
        ZipEntry e = new ZipEntry(name);
        zos.putNextEntry(e);
        zos.write(data);
        zos.closeEntry();
    }

    static byte[] readFile(File f) throws IOException {
        try (InputStream in = new java.io.FileInputStream(f)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            return bos.toByteArray();
        }
    }

    static void writeFile(File f, byte[] data) throws IOException {
        try (OutputStream os = new FileOutputStream(f)) { os.write(data); }
    }

    static String guessExt(byte[] d) {
        if (d.length > 4 && d[0] == (byte) 0x89 && d[1] == 'P') return ".png";
        if (d.length > 3 && d[0] == 'G' && d[1] == 'I') return ".gif";
        return ".jpg";
    }

    static String esc(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    static String safe(String s) {
        String t = s.replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "-").trim();
        if (t.length() > 80) t = t.substring(0, 80);
        return t.isEmpty() ? "网页导出" : t;
    }

    static void deleteRec(File f) {
        if (f == null || !f.exists()) return;
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteRec(k);
        f.delete();
    }

    static String saveToDownloads(Context ctx, File src, String name) throws IOException {
        if (Build.VERSION.SDK_INT >= 29) {
            ContentResolver cr = ctx.getContentResolver();
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, name);
            v.put(MediaStore.Downloads.MIME_TYPE, mime(name));
            v.put(MediaStore.Downloads.IS_PENDING, 1);
            Uri uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IOException("无法写入下载目录");
            try (OutputStream os = cr.openOutputStream(uri); InputStream in = new java.io.FileInputStream(src)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) os.write(buf, 0, n);
            }
            v.clear();
            v.put(MediaStore.Downloads.IS_PENDING, 0);
            cr.update(uri, v, null, null);
            return "下载/" + name;
        }
        File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
        File dst = new File(dir, name);
        writeFile(dst, readFile(src));
        return dst.getAbsolutePath();
    }

    static String mime(String name) {
        if (name.endsWith(".epub")) return "application/epub+zip";
        if (name.endsWith(".pdf")) return "application/pdf";
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    }
}
