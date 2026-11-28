package honmei

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import java.net.InetSocketAddress
import java.net.Proxy
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

object Http {
    /** 手机上一般开着 VPN，直连即可；设置里填了代理（如 http://127.0.0.1:7890）就走代理。 */
    fun proxy(): Proxy? {
        val p = Settings.load()["proxy"].str().trim()
        if (p.isEmpty()) return null
        return try {
            val u = java.net.URI(if ("://" in p) p else "http://$p")
            val type = if (u.scheme?.startsWith("socks") == true) Proxy.Type.SOCKS else Proxy.Type.HTTP
            Proxy(type, InetSocketAddress(u.host, if (u.port > 0) u.port else 7890))
        } catch (e: Exception) { null }
    }

    fun builder(timeoutSec: Long = 15): OkHttpClient.Builder = OkHttpClient.Builder()
        .connectTimeout(timeoutSec, TimeUnit.SECONDS)
        .readTimeout(timeoutSec, TimeUnit.SECONDS)
        .callTimeout(timeoutSec * 2, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .apply { proxy()?.let { proxy(it) } }

    val shared: OkHttpClient by lazy { builder(10).build() }

    fun md5hex(s: String): String = MessageDigest.getInstance("MD5").digest(s.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
}

/** 内存里的 Cookie，按域名存。 */
class MemoryCookieJar : CookieJar {
    private val store = HashMap<String, MutableMap<String, Cookie>>()
    val extra = LinkedHashMap<String, String>()   // 手动加的（禁漫登录后的 AVS）

    @Synchronized override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        val m = store.getOrPut(url.host) { LinkedHashMap() }
        for (c in cookies) m[c.name] = c
    }

    @Synchronized override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val out = ArrayList<Cookie>()
        store[url.host]?.values?.let { out.addAll(it) }
        val names = out.map { it.name }.toSet()
        for ((k, v) in extra) if (k !in names) out.add(Cookie.Builder().name(k).value(v).domain(url.host).build())
        return out
    }

    @Synchronized fun hasAny(): Boolean = store.values.any { it.isNotEmpty() }
    @Synchronized fun copyTo(host: String, from: String) { store[from]?.let { store[host] = LinkedHashMap(it) } }
}

/** 同一站点的请求最小间隔，避免请求过快被限流。 */
class Throttle(private val intervalSec: Double) {
    private var last = 0L
    @Synchronized fun await() {
        val gap = last + (intervalSec * 1000).toLong() - System.currentTimeMillis()
        if (gap > 0) Thread.sleep(gap)
        last = System.currentTimeMillis()
    }
}
