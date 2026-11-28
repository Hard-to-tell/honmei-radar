package honmei.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as HonmeiApplication
        val server = app.startServer()
        val base = "http://127.0.0.1:${server.listeningPort}"

        web = WebView(this)
        web.setBackgroundColor(Color.parseColor("#0C0C0C"))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setSupportMultipleWindows(false)
            mediaPlaybackRequiresUserGesture = true
            textZoom = 100
        }
        web.isVerticalScrollBarEnabled = false
        // 口令放在 Cookie 里，只有这个 WebView 能用本地接口
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setCookie(base, "${honmei.Server.COOKIE}=${server.token}; Path=/")
            flush()
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host == "127.0.0.1") return false
                openExternal(u); return true
            }
        }
        web.addJavascriptInterface(Bridge(), "HonmeiApp")

        // 内容不压到状态栏 / 手势条下面
        web.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val b = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                v.setPadding(b.left, b.top, b.right, b.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop, insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        setContentView(web)
        if (savedInstanceState != null) web.restoreState(savedInstanceState) else web.loadUrl("$base/")
    }

    private fun openExternal(u: Uri) {
        try { startActivity(Intent(Intent.ACTION_VIEW, u)) }
        catch (e: Exception) { Toast.makeText(this, "没有能打开这个链接的应用", Toast.LENGTH_SHORT).show() }
    }

    inner class Bridge {
        @JavascriptInterface fun copy(text: String) {
            runOnUiThread {
                (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("honmei", text))
            }
        }

        @JavascriptInterface fun share(text: String) {
            runOnUiThread {
                val i = Intent(Intent.ACTION_SEND).apply { type = "text/plain"; putExtra(Intent.EXTRA_TEXT, text) }
                startActivity(Intent.createChooser(i, "发给…"))
            }
        }

        /** 导入 / 开卷进行中保持亮屏，免得锁屏后被系统挂起。 */
        @JavascriptInterface fun busy(on: Boolean) {
            runOnUiThread {
                if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }

        @JavascriptInterface fun tick() { runOnUiThread { web.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK) } }

        @JavascriptInterface fun open(url: String) { runOnUiThread { openExternal(Uri.parse(url)) } }
    }

    override fun onSaveInstanceState(outState: Bundle) { super.onSaveInstanceState(outState); web.saveState(outState) }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        web.evaluateJavascript("(window.honmeiBack && window.honmeiBack()) ? 1 : 0") { r ->
            if (r != "1") {
                if (web.canGoBack()) web.goBack() else moveTaskToBack(true)
            }
        }
    }

    override fun onResume() { super.onResume(); web.onResume() }
    override fun onPause() { web.onPause(); super.onPause() }
}
