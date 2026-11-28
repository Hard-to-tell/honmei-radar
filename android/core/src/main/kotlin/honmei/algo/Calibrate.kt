package honmei.algo

import honmei.*
import kotlin.math.sqrt

/** 口味调音：每轮挑 5 本让你打分（对应 calibrate.py）。 */
object Calibrate {
    const val ROUND_SIZE = 5
    const val SKIP_COOLDOWN = 7 * 24 * 3600.0
    val SOURCE_NAMES = mapOf("fav" to "你的收藏", "rec" to "最近的推荐", "random" to "站内随手翻到")

    fun currentRound(db: DB): Int = db.q("SELECT MAX(round) r FROM calibration WHERE level > 0")[0]["r"].int() + 1

    private fun blocked(db: DB): Set<Pair<String, String>> {
        val t = now()
        return db.calibrationMap().filter { it.value.level > 0 || t - it.value.at < SKIP_COOLDOWN }.keys
    }

    fun card(w: Work, source: String, cluster: String = ""): MutableMap<String, Any?> = linkedMapOf(
        "site" to w.site, "wid" to w.wid, "title" to Norm.parseTitle(w.title).core.ifEmpty { w.title }, "full_title" to w.title,
        "authors" to w.authors.take(2), "tags" to w.tags.take(8), "cover" to "/api/cover/${w.site}/${w.wid}",
        "url" to w.url, "source" to source, "source_name" to SOURCE_NAMES[source], "cluster" to cluster,
        "site_name" to if (w.site == "jm") "禁漫" else "哔咔",
    )

    private fun clusterName(taste: TasteModel, w: Work): String {
        if (taste.clusters.isEmpty()) return ""
        val ci = taste.clusterOf(listOf(w))[0]
        return if (ci >= 0) taste.clusters[ci].name else ""
    }

    fun pickRound(db: DB, seed: Long? = null): Map<String, Any?> {
        val rnd = java.util.Random(seed ?: System.nanoTime())
        val rno = currentRound(db)
        val profile = Recommend.buildProfile(db)
        if (profile.empty) return mapOf("round" to rno, "items" to emptyList<Any>(), "message" to "先导入收藏，再来调音。")
        val taste = Recommend.buildTaste(db, profile)
        val blocked = blocked(db)
        val favs = profile.favorites; val newness = profile.favWeights
        val cards = ArrayList<MutableMap<String, Any?>>(); val used = HashSet<Pair<String, String>>()
        fun add(i: Int) {
            val w = favs[i]
            if (w.key in blocked || w.key in used) return
            used.add(w.key)
            val name = taste.clusters.lastOrNull { i in it.members }?.name ?: ""
            cards.add(card(w, "fav", name))
        }
        val nFav = ROUND_SIZE - if (rno >= 3) 2 else 0
        val dist = taste.clusterDistances(favs)
        if (taste.clusters.isEmpty() || dist == null) {
            for (i in favs.indices.sortedByDescending { newness[it] }) { if (cards.size >= nFav) break; add(i) }
        } else {
            val memberOf = HashMap<Int, Int>()
            for ((ci, c) in taste.clusters.withIndex()) for (i in c.members) memberOf[i] = ci
            if (rno == 1) {
                var passes = 0
                while (cards.size < nFav && passes < 3) {
                    for ((ci, c) in taste.clusters.withIndex()) {
                        if (cards.size >= nFav) break
                        val mem = c.members.filter { favs[it].key !in blocked && favs[it].key !in used }
                        if (mem.isEmpty()) continue
                        add(mem.minBy { dist[it, ci] - 0.15 * newness[it] })
                    }
                    passes++
                }
            } else {
                val pr = favs.indices.associateWith { i ->
                    val ci = memberOf[i] ?: -1
                    val c = if (ci >= 0) taste.clusters[ci] else null
                    val n = c?.nSignals ?: 0
                    val mixed = if (n >= 2) (c?.uncertainty ?: 1.0) - 1 / sqrt(n + 1.0) else 0.0
                    val d = (0 until dist.cols).map { dist[i, it] }.sorted()
                    val amb = if (d.size > 1) d[0] / maxOf(d[1], 1e-6) else 0.0
                    3 * mixed + (if (n == 0) 1.0 else 0.0) + 0.8 * amb + 0.2 * newness[i] + rnd.nextDouble() * 0.2
                }
                val perC = HashMap<Int, Int>()
                for (i in favs.indices.sortedByDescending { pr.getValue(it) }) {
                    if (cards.size >= nFav) break
                    val ci = memberOf[i] ?: -1
                    if ((perC[ci] ?: 0) >= 2 || favs[i].key in blocked) continue
                    val before = cards.size
                    add(i)
                    if (cards.size > before) perC.merge(ci, 1, Int::plus)
                }
            }
        }
        if (rno >= 3) {
            val favKeys = favs.map { it.key }.toSet()
            val fb = db.feedbackMap()
            val recent = db.q("SELECT site, wid FROM batch_items WHERE batch_id IN (SELECT id FROM batches ORDER BY id DESC LIMIT 3) ORDER BY batch_id DESC, pos")
            for (r in recent) {
                val k = r["site"].str() to r["wid"].str()
                val w = db.getWork(k.first, k.second)
                if (w != null && k !in blocked && k !in favKeys && k !in fb && k !in used) {
                    used.add(k); cards.add(card(w, "rec", clusterName(taste, w))); break
                }
            }
            val pool = TasteData.backgroundWorks(db, setOf("latest", "random")).filter { it.key !in blocked && it.key !in favKeys && it.key !in used }
            if (pool.isNotEmpty()) { val w = pool[rnd.nextInt(pool.size)]; cards.add(card(w, "random", clusterName(taste, w))) }
        }
        return mapOf("round" to rno, "items" to cards.take(ROUND_SIZE))
    }

    fun rate(db: DB, site: String, wid: String, level: Int?, reasons0: List<String>?, rnd: Int, source: String) {
        val allowed = setOf("实用", "艺术", "画风", "题材", "剧情")
        val reasons = (reasons0 ?: emptyList()).filter { it in allowed }.toMutableList()
        if (reasons.any { it in setOf("画风", "题材", "剧情") } && "艺术" !in reasons) reasons.add("艺术")
        db.setCalibration(site, wid, level, reasons, rnd, if (source in SOURCE_NAMES) source else "fav")
    }

    fun roundSummary(db: DB, rno: Int): Map<String, Any?> {
        val cal = db.calibrationMap().filter { it.value.round == rno }
        val items = ArrayList<MutableMap<String, Any?>>()
        for ((k, c) in cal) db.getWork(k.first, k.second)?.let { w ->
            items.add(card(w, c.source).apply { put("level", c.level); put("level_name", TasteModel.LEVEL_NAMES[c.level]); put("reasons", c.reasons) })
        }
        val rated = items.filter { it["level"].int() > 0 }
        if (rated.isEmpty()) return mapOf("round" to rno, "items" to items, "text" to "这一轮全跳过了，没关系，下次再调。")
        val taste = Recommend.buildTaste(db, Recommend.buildProfile(db))
        val byCluster = LinkedHashMap<String, MutableList<Double>>()
        for (it in rated) {
            val w = db.getWork(it["site"].str(), it["wid"].str())
            val name = if (w != null) clusterName(taste, w) else ""
            it["cluster"] = name
            if (name.isNotEmpty()) byCluster.getOrPut(name) { ArrayList() }.add(TasteModel.CALIB_VALUE[it["level"].int()] ?: 0.0)
        }
        val parts = ArrayList<String>()
        if (byCluster.isNotEmpty()) {
            val avg = byCluster.mapValues { it.value.average() }
            val best = avg.maxBy { it.value }.key; val worst = avg.minBy { it.value }.key
            if (avg.getValue(best) >= 0.5) parts.add("这一轮里，你偏爱「$best」这一簇")
            if (worst != best && avg.getValue(worst) <= -0.5) parts.add("「$worst」似乎是误入")
        }
        val lv = rated.groupingBy { it["level"].int() }.eachCount()
        if (parts.isEmpty()) parts.add("评了 ${rated.size} 本：本命 ${lv[4] ?: 0}、喜欢 ${lv[3] ?: 0}、一般 ${lv[2] ?: 0}、不太喜欢 ${lv[1] ?: 0}")
        val rc = LinkedHashMap<String, Int>()
        for (it in rated) for (r in it["reasons"].strList()) rc.merge(r, 1, Int::plus)
        if (rc.isNotEmpty()) parts.add("你更常因为「${rc.maxBy { it.value }.key}」而喜欢")
        val muted = taste.clusters.filter { it.muted }.map { it.name }
        var text = parts.joinToString("；") + "。"
        if (muted.isNotEmpty()) text += "「${muted.joinToString("」「")}」之后会很少出现在推荐里。"
        return mapOf("round" to rno, "items" to items, "text" to text,
            "clusters" to taste.clusters.map { mapOf("name" to it.name, "weight" to it.weight, "muted" to it.muted, "share" to it.share) })
    }
}
