package honmei

import fi.iki.elonen.NanoHTTPD
import okhttp3.Request
import java.io.File
import java.security.SecureRandom

/** 手机里的本地网页服务（只监听 127.0.0.1，对应 server.py）。带一个随机口令，别的 App 访问不了。 */
class Server(val svc: Service, port: Int, val token: String = newToken()) : NanoHTTPD("127.0.0.1", port) {
    companion object {
        const val COOKIE = "honmei_k"
        fun newToken(): String { val b = ByteArray(18); SecureRandom().nextBytes(b); return b.joinToString("") { "%02x".format(it) } }
        private val coverFailed = java.util.concurrent.ConcurrentHashMap<String, Double>()
        private val TYPES = mapOf("html" to "text/html; charset=utf-8", "js" to "application/javascript; charset=utf-8",
            "css" to "text/css; charset=utf-8", "svg" to "image/svg+xml", "png" to "image/png", "json" to "application/json; charset=utf-8")
    }

    class HttpError(val code: Int, msg: String) : Exception(msg)

    override fun serve(s: IHTTPSession): Response = try {
        if (s.cookies.read(COOKIE) != token && s.parameters["k"]?.firstOrNull() != token) throw HttpError(403, "forbidden")
        route(s)
    } catch (e: HttpError) {
        json(e.code, mapOf("detail" to e.message))
    } catch (e: SiteError) {
        json(400, mapOf("detail" to e.message))
    } catch (e: NoSuchElementException) {
        json(404, mapOf("detail" to e.message))
    } catch (e: IllegalArgumentException) {
        json(400, mapOf("detail" to e.message))
    } catch (e: Throwable) {
        Env.logLine("接口出错 ${s.uri}\n${e.stackTraceToString()}")
        json(500, mapOf("detail" to "${e.javaClass.simpleName}: ${e.message}"))
    }

    private fun json(code: Int, v: Any?): Response =
        newFixedLengthResponse(Response.Status.lookup(code) ?: Response.Status.INTERNAL_ERROR, "application/json; charset=utf-8", Json.stringify(v))
            .apply { addHeader("Cache-Control", "no-store") }

    private fun body(s: IHTTPSession): Map<String, Any?> {
        val len = s.headers["content-length"]?.toIntOrNull() ?: 0
        if (len <= 0) return emptyMap()
        val buf = ByteArray(len); var off = 0
        while (off < len) { val n = s.inputStream.read(buf, off, len - off); if (n < 0) break; off += n }
        return try { Json.parse(String(buf, 0, off, Charsets.UTF_8)).obj() } catch (e: Exception) { emptyMap() }
    }

    private fun static(name: String): Response {
        val bytes = Server::class.java.getResourceAsStream("/honmei/web/$name")?.use { it.readBytes() } ?: throw HttpError(404, "没有这个页面")
        val ext = name.substringAfterLast('.', "html")
        return newFixedLengthResponse(Response.Status.OK, TYPES[ext] ?: "application/octet-stream", bytes.inputStream(), bytes.size.toLong())
            .apply { addHeader("Cache-Control", "no-cache") }
    }

    private fun route(s: IHTTPSession): Response {
        val p = s.uri.trimEnd('/').ifEmpty { "/" }
        val seg = p.trim('/').split('/')
        val get = s.method == Method.GET; val post = s.method == Method.POST
        val q = { k: String -> s.parameters[k]?.firstOrNull() ?: "" }
        if (get && p == "/") return static("index.html")
        if (get && seg[0] == "static" && seg.size == 2) return static(seg[1])
        if (seg[0] != "api") throw HttpError(404, "没有这个页面")
        val db = svc.db
        val r: Any? = when {
            get && p == "/api/status" -> svc.status()
            post && seg.size == 3 && seg[1] == "login" -> {
                val b = body(s); svc.login(seg[2], b["username"].str().trim(), b["password"].str()); mapOf("ok" to true)
            }
            post && seg.size == 3 && seg[1] == "logout" -> { svc.logout(seg[2]); mapOf("ok" to true) }
            post && seg.size == 3 && seg[1] == "import" -> mapOf("job" to svc.startImport(seg[2], body(s)["max_pages"]?.int()))
            post && p == "/api/batches" -> mapOf("job" to svc.startGenerate(body(s)["mode"]?.str()?.ifEmpty { null }))
            post && p == "/api/batches/reset" -> svc.resetBatches()
            get && p == "/api/batches" -> db.listBatches(100)
            get && p == "/api/batches/latest" -> svc.batch(null)
            get && seg.size == 3 && seg[1] == "batches" -> svc.batch(seg[2].toLongOrNull() ?: throw HttpError(404, "批次不存在")) ?: throw HttpError(404, "批次不存在")
            get && p == "/api/jobs" -> svc.jobs.recent()
            get && seg.size == 3 && seg[1] == "jobs" -> svc.jobs.get(seg[2]) ?: throw HttpError(404, "任务不存在")
            post && p == "/api/feedback" -> { val b = body(s); svc.feedback(b["batch_id"].long(), b["pos"].int(), b["label"]?.str()) }
            get && seg.size == 4 && seg[1] == "explain" -> svc.explain(seg[2].toLong(), seg[3].toInt())
            post && p == "/api/tags/block" -> { val b = body(s); mapOf("blocked_tags" to svc.blockTag(b["tag"].str(), b["blocked"]?.bool() ?: true)) }
            get && p == "/api/settings" -> svc.settings()
            post && p == "/api/settings" -> svc.updateSettings(body(s))
            get && p == "/api/calibrate/next" -> svc.calibrateNext()
            post && p == "/api/calibrate" -> {
                val b = body(s); if (b["site"].str().isEmpty() || b["wid"].str().isEmpty()) throw HttpError(400, "缺少作品"); svc.calibrateRate(b)
            }
            get && seg.size == 4 && seg[1] == "calibrate" && seg[2] == "summary" -> svc.calibrateSummary(seg[3].toInt())
            post && p == "/api/honmei" -> {
                val b = body(s); if (b["site"].str().isEmpty() || b["wid"].str().isEmpty()) throw HttpError(400, "缺少作品")
                svc.setHonmei(b["site"].str(), b["wid"].str(), b["on"]?.bool() ?: true); mapOf("ok" to true)
            }
            get && seg.size == 4 && seg[1] == "cover" -> return cover(seg[2], seg[3])
            else -> throw HttpError(404, "没有这个接口")
        }
        return json(200, r)
    }

    /** 封面经本地转发并缓存，避开防盗链和域名变动。 */
    private fun cover(site: String, wid: String): Response {
        val dir = File(Env.dataDir, "covers").apply { mkdirs() }
        val safe = Http.md5hex("$site:$wid")
        val f = File(dir, "$safe.img")
        fun img(bytes: ByteArray, type: String = "image/jpeg") =
            newFixedLengthResponse(Response.Status.OK, type, bytes.inputStream(), bytes.size.toLong()).apply { addHeader("Cache-Control", "max-age=604800") }
        if (f.exists() && f.length() > 0) return img(f.readBytes())
        val w = svc.db.getWork(site, wid) ?: throw HttpError(404, "没有这本")
        if (now() - (coverFailed[safe] ?: 0.0) < 1800) throw HttpError(404, "封面获取失败")
        val urls = try { svc.adapter(site).coverUrls(w) } catch (e: Exception) { listOf(w.cover) }
        val ua = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        for (u in urls.filter { it.startsWith("http") }.distinct()) {
            try {
                Http.shared.newCall(Request.Builder().url(u).header("User-Agent", ua).build()).execute().use { r ->
                    val type = r.header("content-type") ?: ""
                    val bytes = r.body?.bytes()
                    if (r.isSuccessful && bytes != null && bytes.isNotEmpty() && type.startsWith("image")) {
                        f.writeBytes(bytes); return img(bytes, type)
                    }
                }
            } catch (e: Exception) { continue }
        }
        coverFailed[safe] = now()
        throw HttpError(404, "封面获取失败")
    }
}
