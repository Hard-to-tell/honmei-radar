package honmei.algo

import honmei.*
import honmei.sources.SiteAdapter
import java.io.File
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.sqrt

/** 口味模型（对应 taste.py）。 */
object TasteData {
    const val BG_KEY = "background:jm"
    const val BG_TTL = 14 * 24 * 3600.0
    const val BG_RANK = 80
    const val BG_SIZE = 300

    fun backgroundWorks(db: DB, sources: Set<String>? = null): List<Work> {
        val hit = db.kvGet(BG_KEY).obj()
        val src = hit["src"].obj()
        val ids = hit["ids"].strList().filter { sources == null || (src[it]?.str() ?: "rank") in sources }
        return db.getWorks(ids.map { "jm" to it }).values.filter { it.detailOk }
    }

    fun backgroundStale(db: DB): Boolean {
        val hit = db.kvGet(BG_KEY).obj()
        return hit["ids"].arr().isEmpty() || now() - hit["at"].dbl() > BG_TTL
    }

    fun refreshBackground(db: DB, jm: SiteAdapter, progress: Progress?, seed: Long? = null): Int {
        val rnd = java.util.Random(seed ?: System.nanoTime())
        val cats = jm.browseCategories.ifEmpty { listOf("all") }
        val plan = mutableListOf<Pair<String, () -> List<Work>>>(
            "rank" to { jm.ranking("week", 1) }, "rank" to { jm.ranking("month", 1) },
            "latest" to { jm.browse(1) }, "latest" to { jm.browse(2) },
        )
        repeat(2) { val c = cats[rnd.nextInt(cats.size)]; val pg = 2 + rnd.nextInt(39); plan.add("random" to { jm.browse(pg, c) }) }
        val ids = ArrayList<String>(); val src = LinkedHashMap<String, String>(); var nRank = 0
        for ((i, p) in plan.withIndex()) {
            val (kind, fn) = p
            progress.say("在站里随手翻几页，取一份基准样本（${i + 1}/${plan.size}）", i + 1, plan.size)
            val works = try { fn() } catch (e: SiteError) { continue } catch (e: NotImplementedError) { continue }
            for (w in works) {
                if (w.wid in src || (kind == "rank" && nRank >= BG_RANK)) continue
                db.upsertWork(w); ids.add(w.wid); src[w.wid] = kind
                if (kind == "rank") nRank++
            }
        }
        var keepIds: List<String> = ids
        if (ids.size > BG_SIZE) {
            val others = ids.filter { src[it] != "rank" }.shuffled(rnd).take(BG_SIZE - nRank).toSet()
            keepIds = ids.filter { src[it] == "rank" || it in others }
        }
        if (keepIds.isEmpty()) return 0
        Importer.ensureDetails(db, jm, keepIds, progress, "基准样本：")
        db.kvSet(BG_KEY, mapOf("at" to now(), "ids" to keepIds, "src" to keepIds.associateWith { src[it] }))
        return keepIds.size
    }

    /** 两站都有的同一本，统计哔咔标签和禁漫标签的共现，学成同义（哔咔说法 → 禁漫说法）。 */
    fun learnAliases(db: DB, minPairs: Int = 2, minRatio: Double = 0.6): Map<String, String> {
        val pica = HashMap<String, Work>()
        for (w in db.collection("pica")) { val k = Norm.titleKey(w.title); if (k.length >= 4 && w.tags.isNotEmpty()) pica[k] = w }
        val jmVocab = HashSet<String>()
        val pairs = ArrayList<Pair<Set<String>, Set<String>>>()
        for (r in db.q("SELECT * FROM works WHERE site='jm' AND detail_ok=1")) {
            val w = db.rowToWork(r)
            val jt = Norm.normTags(w.tags).toSet()
            jmVocab.addAll(jt)
            val p = pica[Norm.titleKey(w.title)]
            if (p != null && jt.isNotEmpty()) pairs.add(Norm.normTags(p.tags).toSet() to jt)
        }
        val co = LinkedHashMap<Pair<String, String>, Int>(); val nP = HashMap<String, Int>(); val nJ = HashMap<String, Int>()
        for ((pt, jt) in pairs) {
            for (t in pt) nP.merge(t, 1, Int::plus); for (t in jt) nJ.merge(t, 1, Int::plus)
            for (a in pt - jt) for (b in jt - pt) co.merge(a to b, 1, Int::plus)
        }
        val learned = LinkedHashMap<String, String>()
        for ((ab, c) in co.entries.sortedByDescending { it.value }) {
            val (a, b) = ab
            if (a in jmVocab || a in learned || c < minPairs) continue
            if (c.toDouble() / nP.getValue(a) >= minRatio && c.toDouble() / nJ.getValue(b) >= minRatio) learned[a] = b
        }
        val f = File(Env.dataDir, "learned_aliases.json")
        val old = Env.readJsonMap(f)
        if (learned != old) { f.writeText(Json.stringify(learned, 1)); Norm.learnedAliases = learned }
        db.kvSet("aliases:learned", mapOf("at" to now(), "pairs" to pairs.size, "aliases" to learned))
        return learned
    }
}

class TasteModel(
    val favorites: List<Work>, favWeights: DoubleArray, val background: List<Work>,
    feedback: List<Pair<Work, Any>>? = null, clusters: Boolean = true,
    val calibration: Map<Pair<String, String>, DB.Cal> = emptyMap(),
) {
    companion object {
        const val PRIOR = 30.0
        const val FEEDBACK_WEIGHT = 0.8
        val CALIB_MULT = mapOf(4 to 2.0, 3 to 1.3, 2 to 0.5, 1 to 0.0)
        val CALIB_VALUE = mapOf(4 to 1.0, 3 to 0.5, 2 to -0.3, 1 to -1.0)
        val LEVEL_NAMES = mapOf(4 to "本命", 3 to "喜欢", 2 to "一般", 1 to "其实不太喜欢", 0 to "跳过")
        fun sigmoid(x: Double) = 1 / (1 + exp(-x))
    }

    val weights: DoubleArray = DoubleArray(favorites.size) { i -> favWeights[i] * (CALIB_MULT[calibration[favorites[i].key]?.level ?: -1] ?: 1.0) }
    val hasBackground = background.size >= 30
    val pref = HashMap<String, Double>()
    val favRate = HashMap<String, Double>()
    val bgRate = HashMap<String, Double>()
    val feedbackAdj = HashMap<String, Double>()
    val feedback: List<Pair<Work, Any>> = feedback ?: emptyList()
    var clusters: List<Themes.Cluster> = emptyList()
    private var themes: Themes? = null

    init {
        val favTokens = favorites.map { Feat.workTokens(it).toSet() }
        val bgTokens = background.map { Feat.workTokens(it).toSet() }
        val siteVocab = HashMap<String, MutableSet<String>>()
        for ((w, t) in favorites.zip(favTokens)) siteVocab.getOrPut(w.site) { HashSet() }.addAll(t)
        for ((w, t) in background.zip(bgTokens)) siteVocab.getOrPut(w.site) { HashSet() }.addAll(t)
        val siteMass = HashMap<String, Double>()
        for ((i, w) in favorites.withIndex()) siteMass[w.site] = (siteMass[w.site] ?: 0.0) + weights[i]
        val favMass = LinkedHashMap<String, Double>()
        for ((i, toks) in favTokens.withIndex()) for (t in toks) favMass[t] = (favMass[t] ?: 0.0) + weights[i]
        val bgCount = HashMap<String, Int>(); for (toks in bgTokens) for (t in toks) bgCount.merge(t, 1, Int::plus)
        val nBg = background.size
        fun denomOf(t: String) = siteVocab.entries.filter { t in it.value }.sumOf { siteMass[it.key] ?: 0.0 }
        val denom = HashMap<String, Double>(); val rates = ArrayList<Double>()
        for ((t, m) in favMass) { val d = maxOf(denomOf(t), 1e-9); denom[t] = d; rates.add(m / d) }
        val flatRef = if (rates.isNotEmpty()) rates.average() else 0.05
        for (t in favMass.keys + bgCount.keys) {
            if (t in pref) continue
            val pBg = if (hasBackground) ((bgCount[t] ?: 0) + 0.5) / (nBg + 1) else flatRef
            val d = denom[t] ?: denomOf(t)
            val nT = favMass[t] ?: 0.0
            val pFav = if (d > 0) (nT + PRIOR * pBg) / (d + PRIOR) else pBg
            favRate[t] = if (d > 0) nT / d else 0.0
            bgRate[t] = pBg
            var lift = ln(pFav / pBg)
            if (!hasBackground) lift *= 0.5
            pref[t] = lift.coerceIn(-2.0, 2.0)
        }
        if (this.feedback.isNotEmpty()) {
            val want = HashMap<String, Int>(); val skip = HashMap<String, Int>()
            for ((w, lab0) in this.feedback) {
                var label = if (lab0 is Int) (if (lab0 >= 3) "want" else if (lab0 == 1) "skip" else "") else lab0.toString()
                if (label == "seen_like") label = "want"
                for (t in Feat.workTokens(w).toSet()) when (label) {
                    "want" -> want.merge(t, 1, Int::plus)
                    "skip" -> skip.merge(t, 1, Int::plus)
                    "dislike" -> skip.merge(t, 2, Int::plus)
                }
            }
            for (t in want.keys + skip.keys) {
                if (t in feedbackAdj) continue
                val wn = want[t] ?: 0; val sn = skip[t] ?: 0; val n = wn + sn
                val adj = ln((wn + 1.0) / (sn + 1.0)) * n / (n + 3.0)
                feedbackAdj[t] = adj
                pref[t] = ((pref[t] ?: 0.0) + FEEDBACK_WEIGHT * adj).coerceIn(-2.5, 2.5)
            }
        }
        if (clusters) { fitClusters(); clusterSignals() }
    }

    fun rawFit(w: Work, extra: List<String>? = null): Double {
        val toks = Feat.workTokens(w).toSet() + (extra ?: emptyList())
        if (toks.isEmpty()) return 0.0
        return toks.sumOf { pref[it] ?: 0.0 } / sqrt(maxOf(toks.size, 3).toDouble())
    }

    fun fit(w: Work, extra: List<String>? = null): Double = sigmoid(1.5 * rawFit(w, extra))

    fun topReasons(w: Work, n: Int = 3): List<String> =
        Feat.workTokens(w).toSet().filter { (pref[it] ?: 0.0) > 0.2 }.sortedByDescending { pref.getValue(it) }.take(n)

    private fun fitClusters() {
        val th = Themes(favorites, background)
        if (th.clusters.isNotEmpty()) { themes = th; clusters = th.clusters }
    }

    fun clusterOf(ws: List<Work>): List<Int> = themes?.takeIf { ws.isNotEmpty() }?.assign(ws) ?: List(ws.size) { -1 }
    fun clusterMix(ws: List<Work>): Mat? = themes?.takeIf { ws.isNotEmpty() }?.mixture(ws)
    fun clusterDistances(ws: List<Work>): Mat? = themes?.takeIf { ws.isNotEmpty() }?.distances(ws)

    private fun clusterSignals() {
        if (clusters.isEmpty()) return
        val sig = HashMap<Int, MutableList<Double>>(); val levels = HashMap<Int, MutableList<Int>>(); val reasons = HashMap<Int, HashMap<String, Int>>()
        val memberOf = HashMap<Int, Int>()
        for ((ci, c) in clusters.withIndex()) for (i in c.members) memberOf[i] = ci
        for ((i, f) in favorites.withIndex()) {
            val cal = calibration[f.key] ?: continue
            val ci = memberOf[i] ?: continue
            val v = CALIB_VALUE[cal.level] ?: continue
            sig.getOrPut(ci) { ArrayList() }.add(v); levels.getOrPut(ci) { ArrayList() }.add(cal.level)
            for (r in cal.reasons) reasons.getOrPut(ci) { HashMap() }.merge(r, 1, Int::plus)
        }
        if (feedback.isNotEmpty()) {
            val cis = clusterOf(feedback.map { it.first })
            for ((fb, ci) in feedback.zip(cis)) {
                if (ci < 0) continue
                val lab = fb.second
                if (lab is Int) { CALIB_VALUE[lab]?.let { sig.getOrPut(ci) { ArrayList() }.add(it); levels.getOrPut(ci) { ArrayList() }.add(lab) } }
                else sig.getOrPut(ci) { ArrayList() }.add(if (lab == "want" || lab == "seen_like") 0.5 else -0.5)
            }
        }
        for ((ci, c) in clusters.withIndex()) {
            val v = sig[ci] ?: emptyList<Double>()
            val n = v.size
            val mean = v.sum() / (n + 2)
            c.weight = r3(exp(mean).coerceIn(0.3, 2.0))
            val lv = levels[ci] ?: emptyList<Int>()
            c.muted = lv.count { it == 1 } >= 2 && lv.none { it >= 3 }
            val spread = if (n >= 2) { val m = v.average(); sqrt(v.sumOf { (it - m) * (it - m) } / n) } else 0.0
            c.uncertainty = r3(1 / sqrt(n + 1.0) + spread)
            c.nSignals = n
            val rc = reasons[ci] ?: HashMap()
            val tot = maxOf(1, c.members.count { calibration[favorites[it].key]?.reasons?.isNotEmpty() == true })
            c.practical = r2((rc["实用"] ?: 0).toDouble() / tot)
            c.story = r2((rc["剧情"] ?: 0).toDouble() / tot)
            c.art = r2(((rc["艺术"] ?: 0) + (rc["画风"] ?: 0) + (rc["题材"] ?: 0)).toDouble() / tot)
            c.name = c.tags.take(2).joinToString("·")
        }
    }

    fun clusterQueries(perCluster: Int = 2, minShare: Double = 0.06): List<Pair<Int, List<String>>> {
        val out = ArrayList<Pair<Int, List<String>>>(); val used = HashSet<String>()
        for ((ci, c) in clusters.withIndex()) {
            if (c.share < minShare) continue
            val cnt = LinkedHashMap<String, Int>()
            for (i in c.members) for (t in Feat.workTokens(favorites[i])) if (!t.startsWith("原作:")) cnt.merge(t, 1, Int::plus)
            val m = maxOf(1, c.members.size).toDouble()
            fun score(t: String): Double {
                val inside = cnt.getValue(t) / m
                val distinct = minOf(inside / maxOf(favRate[t] ?: 0.0, 0.02), 5.0)
                return inside * distinct * maxOf((pref[t] ?: 0.0) + 0.3, 0.05)
            }
            val ranked = cnt.keys.sortedByDescending { score(it) }.filter { it !in used && cnt.getValue(it) / m >= 0.2 }
            val pick = ranked.take(perCluster)
            used.addAll(pick.take(1))
            if (pick.isNotEmpty()) out.add(ci to pick)
        }
        return out
    }
}
