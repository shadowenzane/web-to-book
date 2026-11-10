package com.webtobook;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private EditText urlInput;
    private CheckBox cbEpub, cbPdf, cbDocx;
    private Button startBtn;
    private ProgressBar bar;
    private TextView status, log;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean running = false;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setTitle("网页成书");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundColor(Color.WHITE);

        root.addView(text("网页成书", 22, true, Color.rgb(30, 30, 30)));
        root.addView(text("粘贴网址 → 抓取正文与图片 → 分章 → 生成 EPUB / PDF / DOCX", 12, false, Color.GRAY));

        urlInput = new EditText(this);
        urlInput.setHint("在此粘贴网页链接，例如 https://mp.weixin.qq.com/s/...");
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setTextSize(13);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        root.addView(urlInput, lp);

        LinearLayout fmt = new LinearLayout(this);
        fmt.setOrientation(LinearLayout.HORIZONTAL);
        fmt.setPadding(0, dp(8), 0, dp(4));
        cbEpub = check("EPUB", true);
        cbPdf = check("PDF", true);
        cbDocx = check("DOCX", true);
        fmt.addView(cbEpub);
        fmt.addView(cbPdf);
        fmt.addView(cbDocx);
        root.addView(fmt);

        startBtn = new Button(this);
        startBtn.setText("开始转换");
        startBtn.setOnClickListener(v -> start());
        root.addView(startBtn, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        root.addView(bar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(10)));

        status = text("就绪", 12, false, Color.DKGRAY);
        root.addView(status);

        ScrollView sc = new ScrollView(this);
        log = text("", 11, false, Color.DKGRAY);
        log.setTypeface(Typeface.MONOSPACE);
        sc.addView(log);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        slp.topMargin = dp(8);
        root.addView(sc, slp);

        TextView maker = text("制作：张兴文 · zane@163.com · v" + Converter.VERSION, 11, false, Color.GRAY);
        maker.setGravity(Gravity.CENTER);
        maker.setPadding(0, dp(8), 0, 0);
        maker.setOnClickListener(v -> showAbout());
        root.addView(maker);

        setContentView(root);

        if (Build.VERSION.SDK_INT < 29) {
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, 1);
        }
        prefillFromClipboard();
    }

    private void showAbout() {
        new android.app.AlertDialog.Builder(this)
                .setTitle("网页成书 v" + Converter.VERSION)
                .setMessage("把网页变成 EPUB / PDF / DOCX。\n\n"
                        + "制作：张兴文\n"
                        + "邮箱：zane@163.com\n"
                        + "主页：" + Converter.HOMEPAGE + "\n\n"
                        + "仅供个人学习与资料归档，版权归原作者及原平台所有。")
                .setPositiveButton("知道了", null)
                .show();
    }

    private void prefillFromClipboard() {
        try {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null && cm.hasPrimaryClip()) {
                ClipData cd = cm.getPrimaryClip();
                if (cd != null && cd.getItemCount() > 0) {
                    CharSequence s = cd.getItemAt(0).coerceToText(this);
                    if (s != null && s.toString().startsWith("http") && s.length() < 500) {
                        urlInput.setText(s.toString().trim());
                    }
                }
            }
        } catch (Exception ignored) { }
    }

    private CheckBox check(String label, boolean on) {
        CheckBox c = new CheckBox(this);
        c.setText(label);
        c.setChecked(on);
        c.setPadding(0, 0, dp(14), 0);
        return c;
    }

    private TextView text(String s, int sp, boolean bold, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        if (bold) t.setTypeface(null, Typeface.BOLD);
        t.setTextColor(color);
        t.setPadding(0, dp(4), 0, dp(4));
        return t;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void logLine(String s) {
        log.append(s + "\n");
    }

    private void start() {
        if (running) return;
        String url = urlInput.getText().toString().trim();
        if (url.isEmpty()) {
            Toast.makeText(this, "请先粘贴网页链接", Toast.LENGTH_SHORT).show();
            return;
        }
        if (!url.startsWith("http")) url = "https://" + url;
        final String u = url;
        final boolean ep = cbEpub.isChecked(), pf = cbPdf.isChecked(), dc = cbDocx.isChecked();
        if (!ep && !pf && !dc) {
            Toast.makeText(this, "请至少选择一种输出格式", Toast.LENGTH_SHORT).show();
            return;
        }
        running = true;
        startBtn.setEnabled(false);
        bar.setProgress(0);
        log.setText("");
        status.setText("正在处理…");
        logLine("开始处理：" + u);

        final Context ctx = this;
        new Thread(() -> {
            try {
                Converter.Result r = Converter.convert(ctx, u, ep, pf, dc, (pct, msg) ->
                        ui.post(() -> {
                            bar.setProgress(pct);
                            status.setText(msg);
                            logLine("· " + msg);
                        }));
                ui.post(() -> {
                    running = false;
                    startBtn.setEnabled(true);
                    bar.setProgress(100);
                    status.setText("完成，文件已保存到「下载」");
                    logLine("标题：" + r.title);
                    for (String n : r.savedNames) logLine("✓ " + n);
                    logLine("共 " + r.chapters + " 章、" + r.images + " 张图片"
                            + (r.failedImages > 0 ? "（失败 " + r.failedImages + " 张）" : ""));
                    for (String e : r.imageErrors) logLine("  图片失败：" + e);
                    Toast.makeText(ctx, "转换完成", Toast.LENGTH_LONG).show();
                });
            } catch (final Exception e) {
                ui.post(() -> {
                    running = false;
                    startBtn.setEnabled(true);
                    status.setText("转换失败");
                    logLine("✗ " + e.toString());
                    Toast.makeText(ctx, "转换失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        }).start();
    }
}
