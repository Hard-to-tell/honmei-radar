package honmei

import honmei.algo.*
import honmei.sources.JmAdapter
import honmei.sources.PicaAdapter
import honmei.sources.SITES
import honmei.sources.SiteAdapter
import java.util.concurrent.ConcurrentHashMap

/** 后台任务（导入、开卷），网页轮询进度。 */
class Jobs {
    val jobs = ConcurrentHashMap<String, MutableMap<String, Any?>>()
    @Volatile var onChange: (Boolean) -> Unit = {}   // 有没有任务在跑（安卓端用来保持亮屏）

    fun running() = jobs.values.any { it["status"] == "running" }

    fun start(kind: String, title: String, fn: (Progress) -> Any?): String {
        synchronized(this) {
            jobs.values.firstOrNull { it["kind"] == kind && it["status"] == "running" }?.let { return it["id"].str() }
            val jid = java.util.UUID.randomUUID().toString().replace("-", "").take(10)
            val t = now()
            val job: MutableMap<String, Any?> = java.util.Collections.synchronizedMap(linkedMapOf<String, Any?>(
                "id" to jid, "kind" to kind, "title" to title, "status" to "running", "progress" to "准备中…",
                "done" to null, "total" to null, "result" to null, "error" to null,
                "started_at" to t, "phase_started_at" to t, "finished_at" to null))
            jobs[jid] = job
            Env.logLine("任务开始 $kind $jid")
            onChange(true)
            val progress: Progress = { msg, done, total ->
                if (total != job["total"]) job["phase_started_at"] = now()
                job["progress"] = msg; job["done"] = done; job["total"] = total
            }
            Thread {
                try {
                    job["result"] = fn(progress); job["status"] = "done"
                    Env.logLine("任务完成 $kind $jid 用时 ${(now() - t).toInt()}s")
                } catch (e: SiteError) {
                    job["error"] = e.message; job["status"] = "error"; Env.logLine("任务失败 $kind $jid：${e.message}")
                } catch (e: Throwable) {
                    job["error"] = "${e.javaClass.simpleName}: ${e.message}"; job["status"] = "error"
                    Env.logLine("任务异常 $kind $jid\n${e.stackTraceToString()}")
                } finally {
                    job["finished_at"] = now()
                    onChange(running())
                }
            }.apply { isDaemon = true; name = "job-$kind" }.start()
            return jid
        }
    }

    fun get(jid: String) = jobs[jid]?.let { synchronized(it) { LinkedHashMap(it) } }

    /** 正在跑的、刚完成的（8 秒内）、出错的（10 分钟内）。 */
    fun recent(): List<Map<String, Any?>> {
        val t = now()
        return jobs.values.map { synchronized(it) { LinkedHashMap(it) } }.filter { j ->
            val age = t - (j["finished_at"] as? Double ?: t)
            j["status"] == "running" || (j["status"] == "done" && age < 8) || (j["status"] == "error" && age < 600)
        }.sortedBy { it["started_at"].dbl() }
    }
}

/** 网页共用的服务层（对应 service.py；去掉了解读、语义模型、网络检测和检验）。 */
class Service(val db: DB) {
    val jobs = Jobs()
    private val adapters = ConcurrentHashMap<String, SiteAdapter>()

    companion object {
        val LABELS = linkedMapOf("want" to "心动", "skip" to "略过", "dislike" to "不喜欢", "seen" to "读过", "seen_like" to "读过·喜欢")
    }

    init {
        try {
            Service::class.java.getResourceAsStream("/honmei/data/near_tags.json")?.use { NearTags.load(it.readBytes().toString(Charsets.UTF_8)) }
        } catch (e: Exception) { Env.logLine("近义标签表没读到：${e.message}") }
    }

    fun adapter(site: String): SiteAdapter = adapters.getOrPut(site) {
        when (site) { "jm" -> JmAdapter(db); "pica" -> PicaAdapter(db); else -> throw SiteError("不认识的站点 $site") }
    }

    fun resetAdapters() = adapters.clear()

    fun status(): Map<String, Any?> {
        val ranker = db.kvGet("ranker").obj()
        val settings = Settings.load()
        val counts = db.collectionCounts()
        return linkedMapOf(
            "demo" to false, "app" to true,
            "sites" to SITES.mapValues { (s, n) ->
                mapOf("name" to n, "logged_in" to adapter(s).loggedIn(), "favorites" to (counts[s] ?: 0), "last_import" to db.kvGet("import:$s"))
            },
            "batches" to db.listBatches(30),
            "rec_mode" to settings["rec_mode"].str().ifEmpty { "balance" },
            "ranker" to (ranker + ("weights_text" to Ranker.describeWeights(ranker))),
            "calibrate" to calibrateState(),
            "clusters" to db.kvGet("taste:clusters"),
            "feedback_counts" to db.q("SELECT label, COUNT(*) n FROM feedback GROUP BY label")
                .associate { (LABELS[it["label"].str()] ?: it["label"].str()) to it["n"].int() },
            "near_tags" to NearTags.enabled,
            "jobs" to jobs.recent(),
        )
    }

    // ---- 调音 ----
    fun calibrateNext() = Calibrate.pickRound(db)

    fun calibrateRate(b: Map<String, Any?>): Map<String, Any?> {
        val lv = b["level"]
        Calibrate.rate(db, b["site"].str(), b["wid"].str(), if (lv == null) null else lv.int(), b["reasons"]?.strList(),
            b["round"].int().takeIf { it > 0 } ?: 1, b["source"].str().ifEmpty { "fav" })
        return mapOf("ok" to true)
    }

    fun calibrateSummary(rno: Int): Map<String, Any?> {
        val r = Calibrate.roundSummary(db, rno)
        db.kvSet("calibrate:last", mapOf("round" to rno, "at" to now(), "batches" to db.listBatches(999).size))
        return r
    }

    fun calibrateState(): Map<String, Any?> {
        val last = db.kvGet("calibrate:last").obj()
        val nBatches = db.listBatches(999).size
        val rated = db.q("SELECT COUNT(*) n FROM calibration WHERE level > 0")[0]["n"].int()
        val hasFavs = db.collectionCounts().values.sum() > 0
        val dot = hasFavs && (rated == 0 || nBatches - last["batches"].int() >= 3)
        return mapOf("round" to Calibrate.currentRound(db), "rated" to rated, "dot" to dot)
    }

    // ---- 登录与导入 ----
    fun login(site: String, u: String, p: String): String = adapter(site).login(u, p)

    fun logout(site: String) {
        for (k in listOf("token", "username", "password")) db.secretSet(site, k, null)
        adapters.remove(site)
    }

    fun startImport(site: String, maxPages: Int? = null): String {
        val ad = adapter(site)
        return jobs.start("import-$site", "导入${SITES[site] ?: site}收藏") { p ->
            val r = Importer.importFavorites(db, ad, p, maxPages)
            try {
                TasteData.learnAliases(db)
                if (TasteData.backgroundStale(db)) TasteData.refreshBackground(db, adapter("jm"), p)
            } catch (e: Exception) { Env.logLine("导入后的基准样本没取全：${e.message}") }
            r
        }
    }

    fun startGenerate(mode: String?): String {
        if (mode != null && mode in Recommend.MODES) Settings.save(mapOf("rec_mode" to mode))
        val jm = adapter("jm")
        return jobs.start("generate", "开卷") { p ->
            val bid = Recommend.generateBatch(db, jm, p, mode0 = mode)
            mapOf("batch_id" to bid, "vol" to bid - db.batchOffset())
        }
    }

    fun resetBatches(): Map<String, Any?> { db.resetBatches(); return mapOf("ok" to true) }

    /** 启动后在后台先算一遍口味，第一次点调音 / 开卷会快一些。 */
    fun warmUp() = Thread {
        try { Thread.sleep(3000); val p = Recommend.buildProfile(db); if (!p.empty) Recommend.buildTaste(db, p) }
        catch (e: Throwable) { Env.logLine("预热失败：${e.message}") }
    }.apply { isDaemon = true }.start()

    // ---- 批次与反馈 ----
    fun batch(bid: Long?): Map<String, Any?>? {
        val id = bid ?: db.listBatches(1).firstOrNull()?.get("id")?.long() ?: return null
        return Recommend.batchView(db, id)
    }

    fun feedback(bid: Long, pos: Int, label: String?): Map<String, Any?> {
        if (label != null && label !in LABELS) throw IllegalArgumentException("label 只能是 want / skip / dislike / seen / seen_like")
        val it = db.batchItems(bid).firstOrNull { it["pos"].int() == pos } ?: throw NoSuchElementException("找不到这张卡")
        db.setFeedback(it["site"].str(), it["wid"].str(), label, bid)
        return mapOf("batch_id" to bid, "pos" to pos, "label" to label)
    }

    fun explain(bid: Long, pos: Int): Map<String, Any?> {
        val ev = Explain.evidence(db, bid, pos) ?: throw NoSuchElementException("找不到这张卡")
        return mapOf("evidence" to ev, "local" to Explain.local(ev))
    }

    // ---- 设置 ----
    fun settings(): Map<String, Any?> = Settings.load()

    fun blockTag(tag0: String, blocked: Boolean): List<String> {
        val tag = tag0.trim()
        val cur = Settings.load()["blocked_tags"].strList().filter { it != tag }.toMutableList()
        if (blocked && tag.isNotEmpty()) cur.add(tag)
        Settings.save(mapOf("blocked_tags" to cur))
        return cur
    }

    fun updateSettings(patch: Map<String, Any?>): Map<String, Any?> {
        val allowed = setOf("batch_size", "explore_ratio", "max_per_author", "rec_mode", "blocked_tags", "card_anim", "proxy", "jm", "pica")
        Settings.save(patch.filterKeys { it in allowed })
        resetAdapters()
        return settings()
    }

    fun setHonmei(site: String, wid: String, on: Boolean) = db.setCalibration(site, wid, if (on) 4 else null, emptyList(), 0, "fav")
}
