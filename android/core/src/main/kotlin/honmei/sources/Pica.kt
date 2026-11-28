package honmei.sources

import honmei.*
import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.InetAddress
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** 哔咔官方接口（picaapi.picacomic.com），登录换 token，token 只存本机。 */
class PicaAdapter(private val db: DB) : SiteAdapter {
    override val site = "pica"
    override val name = "哔咔"

    companion object {
        const val API = "https://picaapi.picacomic.com/"
        const val HOST = "picaapi.picacomic.com"
        const val API_KEY = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
        const val SIGN_KEY = "~d}\$Q7\$eIni=V)9\\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn"
        // 官方客户端的分流地址（Cloudflare 节点）：DNS 被污染时直接连这些 IP，证书照常按域名校验
        val IPS = listOf("172.67.7.24", "172.67.194.19", "172.67.80.1", "104.21.235.3", "104.21.235.4",
            "104.20.180.50", "104.20.181.50", "104.22.64.159", "104.21.91.145")
        val IMAGE_HOSTS = listOf("storage1.picacomic.com", "s2.picacomic.com", "s3.picacomic.com",
            "storage-b.picacomic.com", "storage.tipatipa.xyz", "storage.wikawika.xyz")

        fun signHeaders(pathWithQuery: String, method: String, nowSec: Long = System.currentTimeMillis() / 1000,
                        nonce: String = UUID.randomUUID().toString().replace("-", "")): Map<String, String> {
            val ts = nowSec.toString()
            val raw = (pathWithQuery + ts + nonce + method.uppercase() + API_KEY).lowercase()
            val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(SIGN_KEY.toByteArray(), "HmacSHA256")) }
            val sig = mac.doFinal(raw.toByteArray()).joinToString("") { "%02x".format(it) }
            return linkedMapOf(
                "time" to ts, "nonce" to nonce, "signature" to sig,
                "accept" to "application/vnd.picacomic.com.v1+json", "api-key" to API_KEY, "app-channel" to "2",
                "app-version" to "2.2.1.2.3.3", "app-uuid" to "defaultUuid", "app-platform" to "android",
                "app-build-version" to "44", "image-quality" to "original", "user-agent" to "okhttp/3.8.1",
                "content-type" to "application/json; charset=UTF-8",
            )
        }

        fun thumbUrl(thumb: Map<String, Any?>?): String {
            if (thumb == null || thumb["path"].str().isEmpty()) return ""
            val server = thumb["fileServer"].str().ifEmpty { "https://storage1.picacomic.com" }.trimEnd('/')
            val path = thumb["path"].str().trimStart('/')
            return if ("/static/" in server || path.startsWith("static/")) "$server/$path" else "$server/static/$path"
        }

        @Volatile var pinnedIp: String? = null
    }

    private val settings = Settings.load()
    private val throttle = Throttle(settings["rate_interval"].dbl().takeIf { it > 0 } ?: 0.15)
    private val dns = object : Dns {
        override fun lookup(hostname: String): List<InetAddress> {
            val ip = pinnedIp
            if (hostname == HOST && ip != null) return listOf(InetAddress.getByName(ip))
            return Dns.SYSTEM.lookup(hostname)
        }
    }
    private val client: OkHttpClient = Http.builder(15).dns(dns).build()
    @Volatile private var relogging = false

    /** 直连不通时，挨个试分流 IP。 */
    private fun tryPin(): Boolean {
        if (Http.proxy() != null) return false
        for (ip in IPS) {
            val c = Http.builder(5).dns(object : Dns {
                override fun lookup(hostname: String) = if (hostname == HOST) listOf(InetAddress.getByName(ip)) else Dns.SYSTEM.lookup(hostname)
            }).build()
            try {
                c.newCall(Request.Builder().url(API).header("user-agent", "okhttp/3.8.1").build()).execute().use { }
                pinnedIp = ip
                Env.logLine("哔咔改走分流 $ip")
                return true
            } catch (_: Exception) {}
        }
        return false
    }

    private fun req(method: String, path: String, params: Map<String, Any?>? = null, body: Map<String, Any?>? = null,
                    auth: Boolean = true, retried: Boolean = false): Map<String, Any?> {
        val query = if (params.isNullOrEmpty()) "" else "?" + params.entries.joinToString("&") { "${it.key}=${it.value.str()}" }
        val full = path.trimStart('/') + query
        val h = signHeaders(full, method)
        val rb = Request.Builder().url(API + full)
        h.forEach { (k, v) -> rb.header(k, v) }
        if (auth) {
            val token = db.secretGet("pica", "token") ?: throw SiteError("请先登录哔咔")
            rb.header("authorization", token)
        }
        if (method == "POST") rb.post(Json.stringify(body ?: emptyMap<String, Any?>()).toRequestBody("application/json; charset=UTF-8".toMediaType()))
        throttle.await()
        val (code, text) = try {
            client.newCall(rb.build()).execute().use { it.code to (it.body?.string() ?: "") }
        } catch (e: Exception) {
            if (!retried && pinnedIp == null && tryPin()) return req(method, path, params, body, auth, true)
            throw SiteError("连不上哔咔（${e.javaClass.simpleName}）。确认手机的 VPN 开着，稍后再试。", e)
        }
        val data = try { Json.parse(text).obj() } catch (e: Exception) { throw SiteError("哔咔接口返回异常（HTTP $code）") }
        if (code == 401 && auth) {
            val u = db.secretGet("pica", "username"); val p = db.secretGet("pica", "password")
            if (u != null && p != null && !relogging) {
                relogging = true
                try { login(u, p) } finally { relogging = false }
                return req(method, path, params, body, auth, retried)
            }
            throw SiteError("哔咔登录已过期，请重新登录")
        }
        if (code >= 400 || (data["code"]?.int() ?: 200) >= 400) {
            val msg = data["message"].str().ifEmpty { data["error"].str() }.ifEmpty { code.toString() }
            throw SiteError("哔咔接口错误：$msg")
        }
        return data["data"].obj()
    }

    override fun login(username: String, password: String): String {
        val d = try { req("POST", "auth/sign-in", body = mapOf("email" to username, "password" to password), auth = false) }
        catch (e: SiteError) {
            val m = e.message ?: ""
            throw SiteError(if ("invalid" in m.lowercase() || "password" in m.lowercase() || "1004" in m) "哔咔登录失败：账号或密码不对，再检查一下。" else m)
        }
        val token = d["token"].str()
        if (token.isEmpty()) throw SiteError("哔咔登录失败：没有拿到 token")
        db.secretSet("pica", "token", token)
        db.secretSet("pica", "username", username)
        db.secretSet("pica", "password", password)
        return "ok"
    }

    override fun loggedIn() = db.secretGet("pica", "token") != null

    override fun webUrl(wid: String): String =
        Settings.load()["pica"].obj()["web_url_template"].str().ifEmpty { "https://manhuabika.com/pcomicview/?cid={id}" }.replace("{id}", wid)

    override fun coverUrls(w: Work): List<String> {
        if (w.cover.isEmpty()) return emptyList()
        val u = try { java.net.URI(w.cover) } catch (e: Exception) { return listOf(w.cover) }
        return (listOf(w.cover) + IMAGE_HOSTS.filter { it != u.host }.map { h -> w.cover.replaceFirst(u.host, h) }).distinct()
    }

    private fun fromDoc(d: Map<String, Any?>, detail: Boolean = false): Work {
        val author = d["author"].str()
        val w = Work(site = "pica", wid = (d["_id"] ?: d["id"]).str(), title = d["title"].str(),
            authors = if (author.isNotEmpty()) mutableListOf(author) else ArrayList(),
            tags = (d["categories"].strList() + d["tags"].strList()).toMutableList(),
            description = d["description"].str(), cover = thumbUrl(d["thumb"] as? Map<String, Any?>),
            popularity = (d["totalViews"] ?: d["viewsCount"]).dbl() + 20 * (d["totalLikes"] ?: d["likesCount"]).dbl(),
            detailOk = detail)
        d["chineseTeam"].str().takeIf { it.isNotEmpty() }?.let { w.groups.add(it) }
        w.url = webUrl(w.wid)
        if (author.isNotEmpty()) {
            val pt = Norm.parseTitle("[$author] x")
            if (pt.artists.isNotEmpty()) { w.authors = pt.artists.toMutableList(); w.circles = pt.circles.toMutableList() }
        }
        return enrichFromTitle(w)
    }

    override fun fetchFavorites(progress: Progress?, maxPages: Int?): List<Work> {
        val works = ArrayList<Work>()
        var page = 1; var pages = 1
        while (page <= pages) {
            val d = req("GET", "users/favourite", linkedMapOf("s" to "dd", "page" to page))
            val comics = d["comics"].obj()
            pages = comics["pages"].int().coerceAtLeast(1)
            if (maxPages != null) pages = minOf(pages, maxPages)
            for (doc in comics["docs"].arr()) works.add(fromDoc(doc.obj()))
            progress.say("哔咔：读取收藏列表 第 $page/$pages 页（已读 ${works.size} 本）", page, pages)
            page++
        }
        return works
    }

    override fun fetchDetail(wid: String): Pair<Work, List<Work>> {
        val comic = req("GET", "comics/$wid")["comic"].obj()
        if (comic.isEmpty()) throw SiteError("哔咔作品 $wid 不存在或已下架")
        return fromDoc(comic, true) to emptyList()
    }
}
