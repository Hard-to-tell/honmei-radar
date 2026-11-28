package honmei.sources

import honmei.*
import okhttp3.FormBody
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec
import kotlin.math.ceil

/** 禁漫移动端接口（照 jmcomic 库的 JmApiClient 移植：token 头 + AES 解密，自动换可用域名）。 */
class JmAdapter(private val db: DB) : SiteAdapter {
    override val site = "jm"
    override val name = "禁漫"

    private val settings = Settings.load()
    private val throttle = Throttle(settings["rate_interval"].dbl().takeIf { it > 0 } ?: 0.15)
    private val cookies = MemoryCookieJar()
    private val client: OkHttpClient = Http.builder(15).cookieJar(cookies).build()
    @Volatile private var logged = false
    @Volatile private var ready = false

    companion object {
        const val TOKEN_SECRET = "185Hcomic3PAPP7R"
        const val TOKEN_SECRET_2 = "18comicAPPContent"
        const val DATA_SECRET = "185Hcomic3PAPP7R"
        const val DOMAIN_SERVER_SECRET = "diosfjckwpqpdfjkvnqQjsik"
        @Volatile var appVersion = "2.1.13"
        val DEFAULT_API = listOf("www.cdnhjk.net", "www.cdngwc.cc", "www.cdngwc.net", "www.cdngwc.club")
        val DOMAIN_SERVERS = listOf(
            "https://rup4a04-c01.tos-ap-southeast-1.bytepluses.com/newsvr-2025.txt",
            "https://rup4a04-c02.tos-cn-hongkong.bytepluses.com/newsvr-2025.txt",
            "https://rup4a04-c03.tos-cn-beijing.bytepluses.com.cn/newsvr-2025.txt",
        )
        val IMAGE_DOMAINS = mutableListOf(
            "cdn-msp.jmapiproxy1.cc", "cdn-msp.jmapiproxy2.cc", "cdn-msp2.jmapiproxy2.cc", "cdn-msp3.jmapiproxy2.cc",
            "cdn-msp.jmapinodeudzn.net", "cdn-msp3.jmapinodeudzn.net",
        )
        const val UA = "Mozilla/5.0 (Linux; Android 9; V1938CT Build/PQ3A.190705.11211812; wv) AppleWebKit/537.36 (KHTML, " +
            "like Gecko) Version/4.0 Chrome/91.0.4472.114 Safari/537.36"
        @Volatile private var updatedDomains: List<String>? = null

        fun decode(data: String, ts: String, secret: String = DATA_SECRET): String {
            val raw = Base64.getDecoder().decode(data.trim())
            val key = Http.md5hex(ts + secret).toByteArray(Charsets.UTF_8)
            val c = Cipher.getInstance("AES/ECB/NoPadding")
            c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"))
            val out = c.doFinal(raw)
            val pad = out[out.size - 1].toInt() and 0xff
            return String(out, 0, out.size - pad, Charsets.UTF_8)
        }

        fun num(s: Any?): Double {
            val t = s.str().trim().uppercase()
            val m = Regex("^([\\d.]+)\\s*([KMW万]?)").find(t) ?: return 0.0
            val v = m.groupValues[1].toDoubleOrNull() ?: return 0.0
            return v * when (m.groupValues[2]) { "K" -> 1e3; "M" -> 1e6; "W", "万" -> 1e4; else -> 1.0 }
        }
    }

    private var domains: MutableList<String> = (settings["jm"].obj()["api_domains"].strList().ifEmpty { DEFAULT_API.shuffled() }).toMutableList()

    // ---------- 底层 ----------
    private fun fetchDomains() {
        if (settings["jm"].obj()["api_domains"].strList().isNotEmpty()) return
        updatedDomains?.let { if (it.isNotEmpty()) domains = it.toMutableList(); return }
        for (u in DOMAIN_SERVERS.shuffled()) {
            try {
                val text = client.newCall(Request.Builder().url(u).header("user-agent", UA).build()).execute().use { it.body?.string() ?: "" }
                val t = text.dropWhile { it.code > 127 }
                val obj = Json.parse(decode(t, "", DOMAIN_SERVER_SECRET)).obj()
                val list = obj["Server"].strList()
                if (list.isNotEmpty()) { updatedDomains = list; domains = list.toMutableList(); return }
            } catch (e: Exception) { Env.logLine("禁漫域名更新失败 $u：${e.message}") }
        }
        updatedDomains = emptyList()
    }

    private fun ensureReady() {
        if (ready) return
        synchronized(this) {
            if (ready) return
            fetchDomains()
            try {
                val d = req("/setting").obj()
                d["img_host"].str().takeIf { it.isNotEmpty() }?.let { h ->
                    val dom = h.removePrefix("https://").removePrefix("http://").trimEnd('/')
                    IMAGE_DOMAINS.remove(dom); IMAGE_DOMAINS.add(0, dom)
                }
                val v = d["jm3_version"].str()
                if (v.isNotEmpty() && compareVersions(v, appVersion) > 0) appVersion = v
            } catch (e: Exception) { Env.logLine("禁漫 setting 失败：${e.message}") }
            ready = true
        }
    }

    private fun compareVersions(a: String, b: String): Int {
        val x = a.split('.').map { it.toIntOrNull() ?: 0 }; val y = b.split('.').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) { val c = (x.getOrElse(i) { 0 }).compareTo(y.getOrElse(i) { 0 }); if (c != 0) return c }
        return 0
    }

    /** 请求一个接口，返回解密后的 JSON。按域名轮换重试。 */
    private fun req(path: String, params: Map<String, Any?> = emptyMap(), form: Map<String, String>? = null): Any? {
        var lastErr: Exception? = null
        val order = domains.toList()
        for (attempt in 0 until (order.size * 2).coerceAtLeast(2)) {
            val dom = order[attempt % order.size]
            throttle.await()
            val ts = (System.currentTimeMillis() / 1000).toString()
            val secret = if (path == "/chapter_view_template") TOKEN_SECRET_2 else TOKEN_SECRET
            val ub = "https://$dom$path".toHttpUrl().newBuilder()
            for ((k, v) in params) ub.addQueryParameter(k, v.str())
            val rb = Request.Builder().url(ub.build())
                .header("Accept-Encoding", "gzip, deflate").header("user-agent", UA)
                .header("token", Http.md5hex(ts + secret)).header("tokenparam", "$ts,$appVersion")
            if (form != null) rb.post(FormBody.Builder().apply { form.forEach { (k, v) -> add(k, v) } }.build())
            try {
                client.newCall(rb.build()).execute().use { resp ->
                    val text = resp.body?.string() ?: ""
                    if (resp.code >= 500) throw SiteError("禁漫接口异常（HTTP ${resp.code}）")
                    val j = try { Json.parse(text.trim()).obj() } catch (e: Exception) { throw SiteError("禁漫返回的不是 JSON（HTTP ${resp.code}）") }
                    if (j["code"].int() != 200) {
                        val msg = j["errorMsg"].str().ifEmpty { j["message"].str() }.ifEmpty { "HTTP ${resp.code}" }
                        throw JmApiError(msg)
                    }
                    val data = j["data"]
                    if (data !is String) return data
                    if (domains.firstOrNull() != dom) { domains.remove(dom); domains.add(0, dom) }
                    return Json.parse(decode(data, ts))
                }
            } catch (e: JmApiError) {
                throw e
            } catch (e: Exception) {
                lastErr = e
            }
        }
        throw SiteError(friendlyNet(lastErr), lastErr)
    }

    private class JmApiError(msg: String) : Exception(msg)

    private fun friendlyNet(e: Exception?): String =
        if (e is SiteError) e.message ?: "禁漫接口异常"
        else "连不上禁漫（${e?.javaClass?.simpleName ?: ""}：${e?.message ?: ""}）。确认手机的 VPN 开着，稍等一会儿再试。"

    private fun api(path: String, params: Map<String, Any?> = emptyMap(), form: Map<String, String>? = null): Any? {
        ensureReady()
        if (!logged && form == null) {
            val u = db.secretGet("jm", "username"); val p = db.secretGet("jm", "password")
            if (u != null && p != null) doLogin(u, p)
        }
        return try { req(path, params, form) } catch (e: JmApiError) { throw SiteError("禁漫接口错误：${e.message}") }
    }

    private fun doLogin(username: String, password: String) {
        ensureReady()
        val d = try { req("/login", form = mapOf("username" to username, "password" to password)).obj() }
        catch (e: JmApiError) {
            val t = e.message ?: ""
            throw SiteError(if (listOf("password", "密码", "帐号", "账号", "username", "用户名").any { t.lowercase().contains(it) })
                "禁漫登录失败：用户名或密码不对，再检查一下。" else "禁漫登录失败：${t.take(200)}")
        }
        d["s"].str().takeIf { it.isNotEmpty() }?.let { cookies.extra["AVS"] = it }
        logged = true
    }

    override fun login(username: String, password: String): String {
        doLogin(username, password)
        db.secretSet("jm", "username", username)
        db.secretSet("jm", "password", password)
        return "ok"
    }

    override fun loggedIn(): Boolean = db.secretGet("jm", "username") != null

    // ---------- 转换 ----------
    override fun webUrl(wid: String): String {
        val d = Settings.load()["jm"].obj()["web_domain"].str().ifEmpty { "18comic.vip" }
        return "https://$d/album/$wid/"
    }

    fun coverUrl(wid: String, domain: String = IMAGE_DOMAINS.random()): String = "https://$domain/media/albums/${wid}_3x4.jpg"

    override fun coverUrls(w: Work): List<String> =
        (listOfNotNull(w.cover.takeIf { it.isNotEmpty() }) + IMAGE_DOMAINS.shuffled().take(3).map { coverUrl(w.wid, it) }).distinct()

    private fun fromItem(aid: String, info: Map<String, Any?>): Work {
        val author = info["author"]
        val authors = if (author is List<*>) author.map { it.str() } else
            author.str().split(Regex("[,，、]")).map { it.trim() }.filter { it.isNotEmpty() }
        val cat = info["category"]; val sub = info["category_sub"]
        val w = Work(site = "jm", wid = aid, title = info["name"].str(), authors = authors.toMutableList(),
            tags = info["tags"].strList().toMutableList(),
            category = (if (cat is Map<*, *>) cat.obj()["title"].str() else cat.str()),
            description = info["description"].str(), cover = coverUrl(aid), url = webUrl(aid))
        for (t in listOf(w.category, if (sub is Map<*, *>) sub.obj()["title"].str() else "")) if (t.isNotEmpty() && t !in w.tags) w.tags.add(t)
        return enrichFromTitle(w)
    }

    private fun pageItems(list: Any?): List<Work> = list.arr().map { it.obj() }.filter { it["id"] != null }.map { fromItem(it["id"].str(), it) }

    // ---------- 收藏 ----------
    override fun fetchFavorites(progress: Progress?, maxPages: Int?): List<Work> {
        if (!loggedIn()) throw SiteError("请先登录禁漫")
        val works = ArrayList<Work>()
        var page = 1; var total = 1
        while (page <= total) {
            val d = try { api("/favorite", linkedMapOf("page" to page, "folder_id" to "0", "o" to "mr")).obj() }
            catch (e: SiteError) { throw SiteError("读取禁漫收藏第 $page 页失败：${e.message}") }
            total = maxOf(1, ceil(d["total"].dbl() / 20.0).toInt())
            if (maxPages != null) total = minOf(total, maxPages)
            works.addAll(pageItems(d["list"]))
            progress.say("禁漫：读取收藏列表 第 $page/$total 页（已读 ${works.size} 本）", page, total)
            page++
        }
        return works
    }

    // ---------- 详情 ----------
    override fun fetchDetail(wid: String): Pair<Work, List<Work>> {
        val a = try { api("/album", mapOf("id" to wid)).obj() } catch (e: SiteError) { throw SiteError("读取禁漫作品 $wid 失败：${e.message}") }
        if (a["name"] == null) throw SiteError("禁漫作品 $wid 不存在或已下架")
        val aid = a["id"].str().ifEmpty { wid }
        val w = Work(site = "jm", wid = aid, title = a["name"].str().trim(), authors = a["author"].strList().toMutableList(),
            tags = a["tags"].strList().toMutableList(), parodies = a["works"].strList().toMutableList(),
            description = a["description"].str().trim(), cover = coverUrl(aid), url = webUrl(aid),
            popularity = num(a["total_views"]) + 20 * num(a["likes"]), detailOk = true)
        enrichFromTitle(w)
        val related = a["related_list"].arr().map { it.obj() }.filter { it["id"] != null }.map { fromItem(it["id"].str(), it) }
        return w to related
    }

    // ---------- 检索 ----------
    private val MODES = mapOf("site" to 0, "work" to 1, "author" to 2, "tag" to 3)
    private val ORDERS = mapOf("latest" to "mr", "view" to "mv", "like" to "tf", "score" to "tr")

    override fun search(query: String, mode: String, page: Int, order: String): List<Work> {
        val d = try {
            api("/search", linkedMapOf("main_tag" to (MODES[mode] ?: 0), "search_query" to query, "page" to page,
                "o" to (ORDERS[order] ?: "mr"), "t" to "a")).obj()
        } catch (e: SiteError) { throw SiteError("禁漫搜索“$query”失败：${e.message}") }
        val redirect = d["redirect_aid"]
        if (redirect != null) {
            return try { listOf(fetchDetail(redirect.str()).first) } catch (e: Exception) { emptyList() }
        }
        return pageItems(d["content"])
    }

    override val browseCategories = listOf("doujin", "single", "short", "another", "hanManga")
    private val CATS = mapOf("all" to "0", "doujin" to "doujin", "single" to "single", "short" to "short", "another" to "another", "hanManga" to "hanman")
    private val BORDERS = mapOf("latest" to "mr", "view" to "mv", "like" to "tf")

    private fun categoriesFilter(page: Int, time: String, category: String, orderBy: String): List<Work> {
        val o = if (time != "a") "${orderBy}_$time" else orderBy
        val d = api("/categories/filter", linkedMapOf("page" to page, "order" to "", "c" to category, "o" to o)).obj()
        return pageItems(d["content"])
    }

    override fun browse(page: Int, category: String, order: String): List<Work> = try {
        categoriesFilter(page, "a", CATS[category] ?: "0", BORDERS[order] ?: "mr")
    } catch (e: SiteError) { throw SiteError("浏览禁漫分类失败：${e.message}") }

    override fun ranking(kind: String, page: Int): List<Work> = try {
        categoriesFilter(page, if (kind == "week") "w" else "m", "0", "mv")
    } catch (e: SiteError) { throw SiteError("读取禁漫排行失败：${e.message}") }
}
