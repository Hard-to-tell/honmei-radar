package honmei.algo

import honmei.*
import kotlin.math.ln

/** 口味主题：NMF 软分组（对应 themes.py）。 */
class Themes(val works: List<Work>, background: List<Work>? = null, val exclude: Set<String> = emptySet(), kFixed: Int? = null) {
    companion object {
        val FORMAT_TAGS = setOf(
            "长篇", "短篇", "短篇合集", "中篇", "同人", "全彩", "彩色", "黑白", "cg杂图", "cg", "连载", "连载中", "完结",
            "单行本", "杂志", "webtoon", "生肉", "英语 eng", "english", "汉化", "无修正", "有修正", "dl版", "图集",
        )
        private val EVENT = Regex("^(c\\d{2,3}|comic\\d.*|comic1.*|\\d+.*)$")
        const val MIN_DF = 3; const val MAX_DF = 0.5; const val SITE_MIN_RATE = 0.01
        const val K_MIN = 3; const val K_MAX = 8; const val MIN_SHARE = 0.03; const val MAX_OVERLAP = 0.5

        fun themeTokens(w: Work, exclude: Set<String>? = null): Set<String> =
            Feat.workTokens(w).filter { !it.startsWith("原作:") && it !in FORMAT_TAGS && !EVENT.containsMatchIn(it) && (exclude == null || it !in exclude) }.toSet()

        fun rowNorm(w: Mat): Mat {
            val m = w.copy(); val s = w.rowSums()
            for (i in 0 until m.rows) for (j in 0 until m.cols) m[i, j] = if (s[i] > 0) m[i, j] / s[i] else 0.0
            return m
        }
    }

    class Cluster(val label: Int, val size: Int, val share: Double, val tags: List<String>, val members: List<Int>) {
        var weight = 1.0; var muted = false; var uncertainty = 1.0; var nSignals = 0
        var practical = 0.0; var story = 0.0; var art = 0.0; var name = tags.take(2).joinToString("·")
    }

    var clusters: List<Cluster> = emptyList()
    var nmf: Nmf? = null
    var vocab: Map<String, Int> = emptyMap()
    private var idf: Map<String, DoubleArray> = emptyMap()
    private var defaultIdf = DoubleArray(0)
    var k = 0
    private var order: List<Int> = emptyList()
    var favMix: Mat = Mat(0, 0)

    init { fit(background ?: emptyList(), kFixed) }

    private fun fit(background: List<Work>, kFixed: Int?) {
        val n = works.size
        if (n < 20) return
        val docs = works.map { themeTokens(it, exclude) }
        val sites = works.map { it.site }
        val df = HashMap<String, Int>(); for (d in docs) for (t in d) df.merge(t, 1, Int::plus)
        val siteN = LinkedHashMap<String, Int>(); for (s in sites) siteN.merge(s, 1, Int::plus)
        val siteDf = siteN.keys.associateWith { HashMap<String, Int>() }
        for ((d, s) in docs.zip(sites)) for (t in d) siteDf.getValue(s).merge(t, 1, Int::plus)
        val bgDf = HashMap<Pair<String, String>, Int>(); val bgSite = HashMap<String, Int>()
        for (w in background) { bgSite.merge(w.site, 1, Int::plus); for (t in themeTokens(w, exclude)) bgDf.merge(w.site to t, 1, Int::plus) }
        val both = siteN.filter { it.value >= 20 }.keys.toList()
        fun usable(t: String): Boolean {
            val c = df.getValue(t)
            if (c < MIN_DF || c.toDouble() / n > MAX_DF) return false
            if (both.size >= 2) for (s in both) {
                val rate = ((siteDf.getValue(s)[t] ?: 0) + (bgDf[s to t] ?: 0)).toDouble() / (siteN.getValue(s) + (bgSite[s] ?: 0))
                if (rate < SITE_MIN_RATE) return false
            }
            return true
        }
        val vlist = df.keys.filter { usable(it) }.sorted()
        if (vlist.size < 5) return
        vocab = vlist.withIndex().associate { it.value to it.index }
        idf = siteN.mapValues { (s, cnt) -> DoubleArray(vlist.size) { ln((cnt + 1.0) / ((siteDf.getValue(s)[vlist[it]] ?: 0) + 1.0)) + 1 } }
        defaultIdf = DoubleArray(vlist.size) { ln((n + 1.0) / (df.getValue(vlist[it]) + 1.0)) + 1 }
        val x = matrix(docs, sites)
        val keep = (0 until x.rows).filter { i -> (0 until x.cols).any { x[i, it] > 0 } }
        if (keep.size < 20) return
        val xk = x.selectRows(keep)
        var best: Nmf? = null
        val ks = if (kFixed != null) listOf(kFixed) else (minOf(K_MAX, maxOf(K_MIN, keep.size / 25)) downTo K_MIN).toList()
        for (kk in ks) {
            val model = Nmf(kk)
            val w = model.fitTransform(xk)
            val h = model.components
            val mix = rowNorm(w)
            val share = DoubleArray(kk) { c -> (0 until mix.rows).sumOf { mix[it, c] } / mix.rows }
            val hn = h.l2rows(eps = 1e-9)
            var overlap = 0.0
            if (kk > 1) { val g = hn.mulT(hn); for (i in 0 until kk) for (j in 0 until kk) if (i != j) overlap = maxOf(overlap, g[i, j]) }
            best = model
            if ((share.minOrNull() ?: 0.0) >= MIN_SHARE && overlap < MAX_OVERLAP) break
        }
        nmf = best; k = best!!.k
        order = (0 until k).toList()
        val mixRaw = mixtureOfDocs(docs, sites)        // 这时 order 还是原次序
        val label = IntArray(n) { i -> if ((0 until k).sumOf { mixRaw[i, it] } > 0) (0 until k).maxByOrNull { mixRaw[i, it] }!! else -1 }
        val out = (0 until k).map { c ->
            val members = (0 until n).filter { label[it] == c }
            val h = best.components.row(c)
            Cluster(c, members.size, r3((0 until n).sumOf { mixRaw[it, c] } / n),
                h.indices.sortedByDescending { h[it] }.take(6).filter { h[it] > 0 }.map { vlist[it] }, members)
        }
        order = (0 until k).sortedByDescending { out[it].share }
        clusters = order.map { out[it] }
        favMix = reorder(mixRaw)
    }

    private fun reorder(m: Mat): Mat {
        val o = Mat(m.rows, m.cols)
        for (i in 0 until m.rows) for ((c, src) in order.withIndex()) o[i, c] = m[i, src]
        return o
    }

    private fun matrix(docs: List<Set<String>>, sites: List<String>): Mat {
        val x = Mat(docs.size, vocab.size)
        for ((i, d) in docs.withIndex()) {
            val idfv = idf[sites[i]] ?: defaultIdf
            for (t in d) vocab[t]?.let { x[i, it] = idfv[it] }
        }
        return x.l2rows(eps = 1e-9)
    }

    /** 每本书在各种味道上的占比（行和为 1；对不上任何味道全是 0）。列按 clusters 的次序。 */
    fun mixtureOfDocs(docs: List<Set<String>>, sites: List<String>): Mat {
        val m = nmf ?: return Mat(docs.size, 0)
        val w = m.transform(matrix(docs, sites))
        return reorder(rowNorm(w))
    }

    fun mixture(ws: List<Work>): Mat = mixtureOfDocs(ws.map { themeTokens(it, exclude) }, ws.map { it.site })

    fun assign(ws: List<Work>): List<Int> {
        if (nmf == null || ws.isEmpty()) return List(ws.size) { -1 }
        val mix = mixture(ws)
        return (0 until mix.rows).map { i -> if ((0 until mix.cols).sumOf { mix[i, it] } > 0) (0 until mix.cols).maxByOrNull { mix[i, it] }!! else -1 }
    }

    fun distances(ws: List<Work>): Mat? {
        if (nmf == null || ws.isEmpty()) return null
        val m = mixture(ws); for (i in m.a.indices) m.a[i] = 1.0 - m.a[i]; return m
    }

    fun outlierScore(ws: List<Work>): DoubleArray {
        val m = nmf ?: return DoubleArray(ws.size) { 1.0 }
        val x = matrix(ws.map { themeTokens(it, exclude) }, ws.map { it.site })
        val r = m.transform(x).mul(m.components)
        val rn = r.rowNorms()
        return DoubleArray(ws.size) { i ->
            var dot = 0.0; for (j in 0 until x.cols) dot += x[i, j] * r[i, j]
            (1.0 - dot / (rn[i] + 1e-9)).coerceIn(0.0, 1.0)
        }
    }
}
