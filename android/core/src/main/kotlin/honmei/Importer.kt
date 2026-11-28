package honmei

import honmei.sources.SiteAdapter
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** 导入收藏 + 并发补详情（对应 importer.py）。 */
object Importer {
    const val DETAIL_TTL = 30 * 24 * 3600.0
    const val WORKERS = 4
    const val MAX_CONSECUTIVE_FAIL = 12

    fun saveRelated(db: DB, site: String, wid: String, related: List<Work>) {
        db.kvSet("related:$site:$wid", related.map { it.wid })
        for (r in related) db.upsertWork(r)
    }

    fun missingDetails(db: DB, site: String, wids: List<String>): List<String> {
        val t = now()
        return wids.filter { wid ->
            val r = db.q("SELECT detail_ok, fetched_at FROM works WHERE site=? AND wid=?", site, wid).firstOrNull()
            r == null || r["detail_ok"].int() == 0 || t - r["fetched_at"].dbl() > DETAIL_TTL
        }
    }

    /** 为缺少详情的作品并发取详情。单本失败不影响其他作品。返回失败数。 */
    fun ensureDetails(db: DB, ad: SiteAdapter, wids: List<String>, progress: Progress?, label: String = ""): Int {
        val todo = missingDetails(db, ad.site, wids.distinct())
        if (todo.isEmpty()) return 0
        var failed = 0; var done = 0; var consecutive = 0
        val stop = AtomicBoolean(false)
        var lastError = ""
        progress.say("${label}补全标签与简介 0/${todo.size}", 0, todo.size)
        val pool = Executors.newFixedThreadPool(WORKERS)
        try {
            val cs = ExecutorCompletionService<Triple<String, Pair<Work, List<Work>>?, String?>>(pool)
            for (wid in todo) cs.submit {
                if (stop.get()) Triple(wid, null, "stopped") else try {
                    Triple(wid, ad.fetchDetail(wid), null)
                } catch (e: SiteError) { Triple(wid, null, e.message ?: "失败") } catch (e: Exception) { Triple(wid, null, "${e.javaClass.simpleName}: ${e.message}") }
            }
            repeat(todo.size) {
                val (_, res, err) = cs.take().get()
                done++
                if (res != null) {
                    val (w, related) = res
                    db.upsertWork(w)
                    if (related.isNotEmpty()) saveRelated(db, ad.site, w.wid, related)
                    consecutive = 0
                } else if (err != "stopped") {
                    failed++; consecutive++; lastError = err ?: ""
                    if (consecutive >= MAX_CONSECUTIVE_FAIL) stop.set(true)
                }
                progress.say("${label}补全标签与简介 $done/${todo.size}", done, todo.size)
            }
        } finally { pool.shutdownNow() }
        if (stop.get()) throw SiteError("${label}连续 $MAX_CONSECUTIVE_FAIL 本取不到详情，已暂停（已完成的会保留，重新导入会接着补）。最后一个错误：$lastError")
        return failed
    }

    fun importFavorites(db: DB, ad: SiteAdapter, progress: Progress?, maxPages: Int? = null, withDetails: Boolean = true): Map<String, Any?> {
        val label = "${ad.name}："
        val works = ad.fetchFavorites(progress, maxPages)
        for (w in works) db.upsertWork(w)
        db.replaceCollection(ad.site, works.map { it.wid })
        db.kvSet("import:${ad.site}", mapOf("at" to now(), "count" to works.size, "detail_failed" to null))
        var failed = 0
        if (withDetails) failed = ensureDetails(db, ad, works.map { it.wid }, progress, label)
        db.kvSet("import:${ad.site}", mapOf("at" to now(), "count" to works.size, "detail_failed" to failed))
        return mapOf("site" to ad.site, "count" to works.size, "detail_failed" to failed)
    }
}
