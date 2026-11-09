import AppKit
import WebKit
import PDFKit

// MARK: - PDF rendering (chapter-wise WebKit snapshot + slice into A4 pages)

enum PDFError: Error {
    case setupFailed
    case loadFailed(String)
    case renderFailed(String)
}

final class PDFRenderer: NSObject, WKNavigationDelegate {
    struct Item { let title: String; let url: URL }

    private let items: [Item]
    private let outURL: URL
    private let completion: (Result<URL, Error>) -> Void

    private var webView: WKWebView!
    private var window: NSWindow!
    private var ctx: CGContext?
    private var index = 0
    private var currentHeight: CGFloat = 0
    private var imageRects: [CGRect] = []
    private var finished = false
    private var pollCount = 0
    private var pageCount = 0

    let pageW: CGFloat = 595.28
    let pageH: CGFloat = 841.89
    let marginLeft: CGFloat = 38
    let marginRight: CGFloat = 38
    let marginTop: CGFloat = 42
    let marginBottom: CGFloat = 42
    var contentW: CGFloat { pageW - marginLeft - marginRight }
    var usableH: CGFloat { pageH - marginTop - marginBottom }

    init(items: [Item], outURL: URL, completion: @escaping (Result<URL, Error>) -> Void) {
        self.items = items
        self.outURL = outURL
        self.completion = completion
        super.init()
    }

    func start() {
        var box = CGRect(x: 0, y: 0, width: pageW, height: pageH)
        guard let c = CGContext(outURL as CFURL, mediaBox: &box, nil) else {
            completion(.failure(PDFError.setupFailed)); return
        }
        ctx = c
        let cfg = WKWebViewConfiguration()
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: contentW, height: 120), configuration: cfg)
        webView.navigationDelegate = self
        window = NSWindow(contentRect: webView.frame, styleMask: [.borderless],
                          backing: .buffered, defer: false)
        window.contentView = webView
        window.orderBack(nil)
        loadNext()
    }

    private func loadNext() {
        if index >= items.count { finishSuccess(); return }
        pollCount = 0
        let u = items[index].url
        webView.loadFileURL(u, allowingReadAccessTo: u.deletingLastPathComponent())
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        waitImages()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail(PDFError.loadFailed(error.localizedDescription))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail(PDFError.loadFailed(error.localizedDescription))
    }

    private func waitImages() {
        let js = "Array.from(document.images).filter(function(i){return !i.complete}).length"
        webView.evaluateJavaScript(js) { value, _ in
            let pending = (value as? Int) ?? 0
            self.pollCount += 1
            if pending == 0 || self.pollCount > 40 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { self.measure() }
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { self.waitImages() }
            }
        }
    }

    private func measure() {
        let js = "JSON.stringify({h: Math.ceil(document.documentElement.scrollHeight),"
            + " rects: (function(){var out=[];"
            + " document.querySelectorAll('img').forEach(function(im){var r=im.getBoundingClientRect();"
            + "   if(r.height>2){out.push([Math.round(r.top+window.scrollY),Math.round(r.bottom+window.scrollY)]);}});"
            + " var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);var n;"
            + " while((n=w.nextNode())){if(!n.nodeValue||!n.nodeValue.trim())continue;"
            + "   var rg=document.createRange();rg.selectNodeContents(n);var rs=rg.getClientRects();"
            + "   for(var i=0;i<rs.length;i++){if(rs[i].height>3){out.push([Math.round(rs[i].top+window.scrollY),"
            + "     Math.round(rs[i].bottom+window.scrollY)]);}}}return out;})()})"
        webView.evaluateJavaScript(js) { value, err in
            guard let s = value as? String,
                  let d = s.data(using: .utf8),
                  let obj = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                  let h = obj["h"] as? Double else {
                self.fail(PDFError.renderFailed("无法测量页面高度"))
                return
            }
            self.currentHeight = max(CGFloat(h), 10)
            if let rects = obj["rects"] as? [[Double]] {
                self.imageRects = rects.compactMap { r in
                    guard r.count == 2, r[1] > r[0] else { return nil }
                    return CGRect(x: 0, y: CGFloat(r[0]), width: 1, height: CGFloat(r[1] - r[0]))
                }
            } else {
                self.imageRects = []
            }
            self.renderChapter()
        }
    }

    private func renderChapter() {
        let cfg = WKPDFConfiguration()
        cfg.rect = CGRect(x: 0, y: 0, width: contentW, height: currentHeight)
        webView.createPDF(configuration: cfg) { result in
            switch result {
            case .success(let data):
                guard let provider = CGDataProvider(data: data as CFData),
                      let doc = CGPDFDocument(provider),
                      let page = doc.page(at: 1) else {
                    self.fail(PDFError.renderFailed("无法生成 PDF 页面"))
                    return
                }
                self.slice(page)
                self.index += 1
                DispatchQueue.main.async { self.loadNext() }
            case .failure(let e):
                self.fail(PDFError.renderFailed(e.localizedDescription))
            }
        }
    }

    private func slice(_ page: CGPDFPage) {
        guard let ctx = ctx else { return }
        let H = currentHeight
        var y: CGFloat = 0
        while y < H - 0.5 {
            var next = min(y + usableH, H)
            if H - next < 40 { next = H }
            if next < H {
                var guardCount = 0
                var adjusted = true
                while adjusted && guardCount < 6 {
                    adjusted = false
                    guardCount += 1
                    for r in imageRects {
                        if r.minY < next && next < r.maxY {
                            if r.maxY - y <= usableH {
                                next = r.maxY
                            } else if r.minY > y + 80 {
                                next = r.minY
                            }
                            adjusted = true
                            break
                        }
                    }
                    if next >= H { next = H; break }
                    if H - next < 40 { next = H; break }
                }
            }
            let sliceH = next - y
            if sliceH < 1 { break }
            ctx.beginPDFPage(nil)
            ctx.saveGState()
            let rect = CGRect(x: marginLeft, y: pageH - marginTop - sliceH,
                              width: contentW, height: sliceH)
            ctx.clip(to: rect)
            ctx.translateBy(x: marginLeft, y: pageH - marginTop - sliceH)
            ctx.translateBy(x: 0, y: -(H - next))
            ctx.drawPDFPage(page)
            ctx.restoreGState()
            ctx.endPDFPage()
            pageCount += 1
            y = next
        }
    }

    private func finishSuccess() {
        guard !finished else { return }
        finished = true
        window?.orderOut(nil)
        window = nil
        webView = nil
        if let ctx = ctx, pageCount > 0 {
            ctx.closePDF()
            ctx_ = nil
            completion(.success(outURL))
        } else {
            ctx?.closePDF()
            try? FileManager.default.removeItem(at: outURL)
            completion(.failure(PDFError.renderFailed("没有生成任何页面")))
        }
    }

    private var ctx_: CGContext?

    private func fail(_ error: Error) {
        guard !finished else { return }
        finished = true
        window?.orderOut(nil)
        window = nil
        webView = nil
        completion(.failure(error))
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var window: NSWindow!
    var urlField: NSTextField!
    var epubCheck: NSButton!
    var pdfCheck: NSButton!
    var docxCheck: NSButton!
    var pathLabel: NSTextField!
    var startButton: NSButton!
    var openButton: NSButton!
    var progress: NSProgressIndicator!
    var statusLabel: NSTextField!
    var logView: NSTextView!

    var outDir: URL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first!

    var process: Process?
    var currentTitle = "网页导出"
    var autotest = false
    var printPages: [URL] = []
    var lastPDFTarget: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)

        if let i = CommandLine.arguments.firstIndex(of: "--autotest"),
           CommandLine.arguments.count > i + 3 {
            autotest = true
            let u = CommandLine.arguments[i + 1]
            let d = CommandLine.arguments[i + 2]
            let f = CommandLine.arguments[i + 3]
            outDir = URL(fileURLWithPath: d)
            pathLabel.stringValue = d
            urlField.stringValue = u
            epubCheck.state = f.contains("epub") ? .on : .off
            pdfCheck.state = f.contains("pdf") ? .on : .off
            docxCheck.state = f.contains("docx") ? .on : .off
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.startConversion() }
            return
        }

        if let s = NSPasteboard.general.string(forType: .string),
           s.lowercased().hasPrefix("http"), s.count < 500,
           !s.contains("\n") {
            urlField.stringValue = s
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 网页成书", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 网页成书", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    @objc private func showAbout() {
        let a = NSAlert()
        a.messageText = "网页成书"
        a.informativeText = "粘贴网页链接，自动抓取正文与图片，生成 EPUB / PDF / DOCX。\n\n版本 1.0"
        a.runModal()
    }

    private func label(_ text: String, size: CGFloat = 13, bold: Bool = false, color: NSColor = .labelColor) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.font = bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size)
        l.textColor = color
        return l
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "网页成书"
        window.center()
        window.setFrameAutosaveName("main")

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 10
        root.edgeInsets = NSEdgeInsets(top: 18, left: 20, bottom: 16, right: 20)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = label("网页成书", size: 20, bold: true)
        let sub = label("粘贴网址 → 抓取正文与图片 → 分章 → 生成 EPUB / PDF / DOCX", size: 12, color: .secondaryLabelColor)
        root.addArrangedSubview(title)
        root.addArrangedSubview(sub)
        root.setCustomSpacing(14, after: sub)

        urlField = NSTextField()
        urlField.placeholderString = "在此粘贴网页链接，例如 https://mp.weixin.qq.com/s/..."
        urlField.font = NSFont.systemFont(ofSize: 13)
        urlField.translatesAutoresizingMaskIntoConstraints = false
        urlField.heightAnchor.constraint(equalToConstant: 26).isActive = true
        root.addArrangedSubview(urlField)
        root.addArrangedSubview(label("支持微信公众号文章；其它网页也会尽力提取正文。", size: 11, color: .tertiaryLabelColor))

        let opts = NSStackView()
        opts.orientation = .horizontal
        opts.spacing = 18
        epubCheck = NSButton(checkboxWithTitle: "EPUB 电子书", target: nil, action: nil)
        pdfCheck = NSButton(checkboxWithTitle: "PDF（A4 分页）", target: nil, action: nil)
        docxCheck = NSButton(checkboxWithTitle: "DOCX 文档", target: nil, action: nil)
        [epubCheck, pdfCheck, docxCheck].forEach { $0!.state = .on }
        opts.addArrangedSubview(epubCheck)
        opts.addArrangedSubview(pdfCheck)
        opts.addArrangedSubview(docxCheck)
        root.addArrangedSubview(label("输出格式", size: 12, color: .secondaryLabelColor))
        root.addArrangedSubview(opts)

        let pathRow = NSStackView()
        pathRow.orientation = .horizontal
        pathRow.spacing = 8
        pathLabel = label(outDir.path, size: 12, color: .secondaryLabelColor)
        pathLabel.lineBreakMode = .byTruncatingMiddle
        let choose = NSButton(title: "更改保存位置…", target: self, action: #selector(chooseFolder))
        choose.bezelStyle = .rounded
        pathRow.addArrangedSubview(label("保存到", size: 12, color: .secondaryLabelColor))
        pathRow.addArrangedSubview(pathLabel)
        pathRow.addArrangedSubview(choose)
        root.addArrangedSubview(pathRow)

        let actionRow = NSStackView()
        actionRow.orientation = .horizontal
        actionRow.spacing = 10
        startButton = NSButton(title: "开始转换", target: self, action: #selector(startConversion))
        startButton.bezelStyle = .rounded
        startButton.keyEquivalent = "\r"
        startButton.controlSize = .large
        openButton = NSButton(title: "打开所在文件夹", target: self, action: #selector(openFolder))
        openButton.bezelStyle = .rounded
        openButton.isEnabled = false
        actionRow.addArrangedSubview(startButton)
        actionRow.addArrangedSubview(openButton)
        root.addArrangedSubview(actionRow)

        progress = NSProgressIndicator()
        progress.isIndeterminate = false
        progress.minValue = 0
        progress.maxValue = 100
        progress.doubleValue = 0
        progress.translatesAutoresizingMaskIntoConstraints = false
        progress.heightAnchor.constraint(equalToConstant: 8).isActive = true
        root.addArrangedSubview(progress)

        statusLabel = label("就绪", size: 12, color: .secondaryLabelColor)
        root.addArrangedSubview(statusLabel)

        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.translatesAutoresizingMaskIntoConstraints = false
        logView = NSTextView()
        logView.isEditable = false
        logView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.textColor = .secondaryLabelColor
        logView.backgroundColor = .textBackgroundColor
        logView.autoresizingMask = [.width]
        logView.isVerticallyResizable = true
        logView.textContainer?.widthTracksTextView = true
        scroll.documentView = logView
        root.addArrangedSubview(scroll)

        window.contentView = root
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
            root.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            root.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor),
            urlField.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -40),
            scroll.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -40),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 150),
        ])
        window.makeKeyAndOrderFront(nil)
    }

    private func log(_ s: String) {
        logView.string += s + "\n"
        logView.scrollToEndOfDocument(nil)
    }

    @objc private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        panel.directoryURL = outDir
        if panel.runModal() == .OK, let u = panel.url {
            outDir = u
            pathLabel.stringValue = u.path
        }
    }

    @objc private func openFolder() {
        NSWorkspace.shared.open(outDir)
    }

    @objc private func startConversion() {
        var url = urlField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if url.isEmpty {
            NSSound.beep()
            statusLabel.stringValue = "请先粘贴网页链接"
            return
        }
        if !url.lowercased().hasPrefix("http") { url = "https://" + url }

        var formats: [String] = []
        if epubCheck.state == .on { formats.append("epub") }
        if pdfCheck.state == .on { formats.append("pdf") }
        if docxCheck.state == .on { formats.append("docx") }
        if formats.isEmpty { formats = ["epub"] }

        guard let script = Bundle.main.resourceURL?.appendingPathComponent("converter/converter.py") else { return }
        guard FileManager.default.fileExists(atPath: script.path) else {
            statusLabel.stringValue = "缺少转换脚本"
            return
        }

        startButton.isEnabled = false
        openButton.isEnabled = false
        progress.doubleValue = 0
        logView.string = ""
        statusLabel.stringValue = "正在处理…"
        printPages = []
        lastPDFTarget = nil

        let needsPDF = formats.contains("pdf")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script.path, "--url", url, "--outdir", outDir.path,
                       "--formats", formats.joined(separator: ",")]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        process = p

        var buffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let d = handle.availableData
            if d.isEmpty { return }
            buffer.append(d)
            while let nl = buffer.firstIndex(of: 0x0A) {
                let lineData = buffer.subdata(in: 0..<nl)
                buffer.removeSubrange(0...nl)
                if let line = String(data: lineData, encoding: .utf8) {
                    DispatchQueue.main.async { self.handleLine(line, needsPDF: needsPDF) }
                }
            }
        }

        p.terminationHandler = { proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                if proc.terminationStatus != 0 {
                    self.statusLabel.stringValue = "转换失败"
                    self.startButton.isEnabled = true
                }
            }
        }

        do {
            try p.run()
            log("开始处理：\(url)")
        } catch {
            statusLabel.stringValue = "无法启动转换进程"
            startButton.isEnabled = true
        }
    }

    private func handleLine(_ line: String, needsPDF: Bool) {
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = obj["type"] as? String else {
            if !line.trimmingCharacters(in: .whitespaces).isEmpty { log(line) }
            return
        }
        switch type {
        case "progress":
            if let pct = obj["pct"] as? Double { progress.doubleValue = pct }
            if let msg = obj["msg"] as? String { statusLabel.stringValue = msg; log("· " + msg) }
        case "meta":
            if let t = obj["title"] as? String {
                currentTitle = t
                log("标题：" + t)
                if let a = obj["author"] as? String, !a.isEmpty { log("作者：" + a) }
                if let d = obj["date"] as? String, !d.isEmpty { log("日期：" + d) }
            }
        case "result":
            if let t = obj["title"] as? String { currentTitle = t }
            var saved: [String] = []
            if let files = obj["files"] as? [String: String] {
                for (k, v) in files.sorted(by: { $0.key < $1.key }) {
                    log("✓ \(k.uppercased())：\(v)")
                    saved.append(v)
                }
            }
            if let checks = obj["checks"] as? [[String: Any]] {
                for c in checks {
                    let fmt = c["fmt"] as? String ?? "?"
                    let ok = c["ok"] as? Bool ?? false
                    if fmt != "PDF" { log("  校验 \(fmt)：\(ok ? "通过" : "有问题")") }
                }
            }
            if needsPDF, let pages = obj["print_pages"] as? [String], !pages.isEmpty {
                printPages = pages.map { URL(fileURLWithPath: $0) }
                if let wd = obj["workdir"] as? String { workdirPath = wd }
                let name = sanitize(currentTitle) + ".pdf"
                lastPDFTarget = outDir.appendingPathComponent(name)
                statusLabel.stringValue = "正在渲染 PDF…"
                progress.doubleValue = 92
                renderPDF()
            } else {
                finishUp(saved: saved, cleanup: obj["workdir"] as? String)
            }
        case "error":
            let msg = obj["msg"] as? String ?? "未知错误"
            statusLabel.stringValue = "转换失败"
            log("✗ " + msg)
            if let detail = obj["detail"] as? String, !detail.isEmpty { log(detail) }
            startButton.isEnabled = true
            if autotest { print("AUTOTEST FAIL: " + msg); exit(1) }
        default:
            break
        }
    }

    private func sanitize(_ s: String) -> String {
        let bad = CharacterSet(charactersIn: "\\/:*?\"<>|\n\r\t")
        let t = s.components(separatedBy: bad).joined(separator: "-")
        return String(t.prefix(80)).isEmpty ? "网页导出" : String(t.prefix(80))
    }

    private func renderPDF() {
        guard let target = lastPDFTarget else { finishUp(saved: [], cleanup: workdirPath); return }
        let items = printPages.enumerated().map { i, u in
            PDFRenderer.Item(title: "第\(i + 1)页组", url: u)
        }
        let renderer = PDFRenderer(items: items, outURL: target) { result in
            switch result {
            case .success(let url):
                self.log("✓ PDF：\(url.path)")
                self.finishUp(saved: [url.path], cleanup: self.workdirPath)
            case .failure(let err):
                self.log("✗ PDF 生成失败：\(err.localizedDescription)")
                self.statusLabel.stringValue = "PDF 生成失败，其它格式已保存"
                self.startButton.isEnabled = true
                self.openButton.isEnabled = true
            }
        }
        self.pdfRenderer = renderer
        renderer.start()
    }

    var pdfRenderer: PDFRenderer?
    var workdirPath: String?

    private func finishUp(saved: [String], cleanup: String?) {
        if let c = cleanup { workdirPath = c }
        if let c = workdirPath {
            try? FileManager.default.removeItem(atPath: c)
            workdirPath = nil
        }
        progress.doubleValue = 100
        statusLabel.stringValue = "完成，已保存到 " + outDir.path
        startButton.isEnabled = true
        openButton.isEnabled = true
        if autotest {
            print("AUTOTEST OK")
            exit(0)
        }
        NSWorkspace.shared.activateFileViewerSelecting([outDir])
    }
}

// MARK: - entry point

let args = CommandLine.arguments
var cliRenderer: PDFRenderer?

func runCLI(url: String, outdir: String, formats: String) {
    guard let script = Bundle.main.resourceURL?.appendingPathComponent("converter/converter.py"),
          FileManager.default.fileExists(atPath: script.path) else {
        print("缺少转换脚本")
        exit(2)
    }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = [script.path, "--url", url, "--outdir", outdir, "--formats", formats]
    var env = ProcessInfo.processInfo.environment
    env["PYTHONIOENCODING"] = "utf-8"
    p.environment = env
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = Pipe()
    do { try p.run() } catch { print("无法启动转换进程"); exit(2) }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    let text = String(data: data, encoding: .utf8) ?? ""
    var resultObj: [String: Any]?
    for line in text.split(separator: "\n") {
        if let d = line.data(using: .utf8),
           let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] {
            if let t = o["type"] as? String, t == "progress", let m = o["msg"] as? String {
                print("· " + m)
            }
            if let t = o["type"] as? String, t == "result" { resultObj = o }
            if let t = o["type"] as? String, t == "error" { print("错误：" + ((o["msg"] as? String) ?? "")) }
        }
    }
    guard let res = resultObj else { print(text); exit(1) }
    if let files = res["files"] as? [String: String] {
        for (_, v) in files { print("✓ " + v) }
    }
    if formats.contains("pdf"), let pages = res["print_pages"] as? [String], !pages.isEmpty {
        let title = (res["title"] as? String) ?? "网页导出"
        let name = title.components(separatedBy: CharacterSet(charactersIn: "\\/:*?\"<>|\n\r\t")).joined(separator: "-")
        let out = URL(fileURLWithPath: outdir).appendingPathComponent(String(name.prefix(80)) + ".pdf")
        let items = pages.map { PDFRenderer.Item(title: "", url: URL(fileURLWithPath: $0)) }
        let r = PDFRenderer(items: items, outURL: out) { result in
            switch result {
            case .success(let u): print("✓ " + u.path)
            case .failure(let e): print("✗ PDF: " + e.localizedDescription)
            }
            exit(0)
        }
        cliRenderer = r
        r.start()
        return
    }
    exit(0)
}

if CommandLine.arguments.count >= 5 && CommandLine.arguments[1] == "--cli" {
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    DispatchQueue.main.async {
        runCLI(url: CommandLine.arguments[2], outdir: CommandLine.arguments[3],
               formats: CommandLine.arguments[4])
    }
    app.run()
} else if args.count >= 4 && args[1] == "--render-pdf" {
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    let src = URL(fileURLWithPath: args[2])
    let out = URL(fileURLWithPath: args[3])
    var urls: [URL] = []
    if let files = try? FileManager.default.contentsOfDirectory(at: src, includingPropertiesForKeys: nil) {
        urls = files.filter { $0.lastPathComponent.hasPrefix("print_") && $0.pathExtension == "html" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }
    if urls.isEmpty { urls = [src] }
    let items = urls.enumerated().map { PDFRenderer.Item(title: "part \($0.offset + 1)", url: $0.element) }
    let renderer = PDFRenderer(items: items, outURL: out) { result in
        switch result {
        case .success(let u):
            print("OK \(u.path)")
            exit(0)
        case .failure(let e):
            print("FAIL \(e.localizedDescription)")
            exit(1)
        }
    }
    renderer.start()
    app.run()
} else {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
