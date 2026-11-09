# 网页成书 · WebToBook

把网页（尤其是微信公众号文章）变成 **EPUB / PDF / DOCX**，支持 macOS、Windows、Android 三端。

> Paste a web page URL → fetch text & images → split chapters → build EPUB / PDF / DOCX.

---

## ✨ 功能

- **一键转换**：粘贴链接即可，自动识别标题、作者、发布日期
- **正文提取**：优先解析微信公众号 `#js_content`，其它网页回退到 `<article>` / `<body>`
- **图片本地化**：正文图片全部下载并以原图最大分辨率嵌入（自动跳过微信装饰性 SVG）
- **自动分章**：按正文加粗小标题切分章节，过短的“空标题”自动并入下一章
- **三种输出**
  - **EPUB 3**：封面、目录、章节导航、内嵌样式，适配「图书」/微信读书/Kindle
  - **PDF**：A4 分页、页边距、页码；桌面版用 WebKit 逐页切片（不截断图片与文字行），Windows 版用 reportlab 排版
  - **DOCX**：Word / Pages 可直接编辑，图片按比例缩放嵌入
- **校验**：EPUB 检查 `mimetype`/XML/清单/图片引用完整性，DOCX 检查 OOXML 结构
- **三端一致**：核心算法同一套，macOS 用 Swift 原生界面，Windows 用 Tkinter，Android 用原生 Java

## 📦 下载

前往 [Releases](../../releases) 或 Actions 的 artifact：

| 平台 | 文件 | 说明 |
|---|---|---|
| macOS 13+ (Apple 芯片) | `网页成书.app` / `.zip` | 已 ad-hoc 签名，双击运行 |
| Windows 10/11 | `网页成书.exe` | 单文件，无需安装 Python |
| Android 8.0+ | `网页成书.apk` | 保存到系统「下载」目录 |

## 🚀 使用

1. 打开应用，在输入框粘贴网页链接（⌘/Ctrl+V）
2. 勾选需要输出的格式（默认 EPUB + PDF + DOCX）
3. 选择保存位置（默认「下载」）
4. 点击「开始转换」，等待进度完成

## 🧱 项目结构

```
core/                  纯 Python 转换内核（无第三方依赖）
  converter.py         抓取 / 解析 / EPUB / DOCX / PDF(reportlab)
  dom.py               轻量 HTML DOM 解析
desktop/macos/         Swift + AppKit 界面，WebKit 渲染 PDF
desktop/windows/       Tkinter 界面（macOS 也可运行）
android/               Android 原生 Java 实现（Jsoup + PdfDocument）
.github/workflows/     三平台自动构建
```

## 🛠 本地构建

**macOS**

```bash
./desktop/macos/build.sh          # 产物：dist/网页成书.app
```

**Windows**

```bat
desktop\windows\build.bat         :: 需要 Python 3.10+
:: 产物：dist\网页成书.exe
```

**Android**

```bash
cd android
gradle assembleRelease            # 需要 JDK 17 + Android SDK 35
# 产物：app/build/outputs/apk/release/app-release.apk
```

**命令行（任意平台）**

```bash
python3 core/converter.py --url "https://mp.weixin.qq.com/s/xxxx" --outdir ~/Downloads \
        --formats epub,pdf,docx --pdf-engine reportlab
```

## ⚠️ 限制

- 仅支持**可公开访问**的页面；需要登录、已被删除或触发平台验证的链接抓不到
- 微信文章的图片托管在 `mmbiz.qpic.cn`，个别图片可能因防盗链失效
- PDF 图片较多的文章页数会比较多（为保证清晰度）
- Android 版在真机上的兼容性仍需更多测试；如遇问题请提 issue
- 请遵守目标网站的版权与使用条款，仅用于个人学习与资料归档

## 📄 License

[MIT](LICENSE)

---

命名：**网页成书**（WebToBook）—— 把网页收拢成一本可以带走的书。
