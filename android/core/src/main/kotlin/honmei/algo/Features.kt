package honmei.algo

import honmei.*
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.ln1p

/** 候选特征与推荐依据（对应 features.py）。 */
object Feat {
    val FEATURES = listOf("tag", "fit", "author", "title", "semantic", "neg", "pop")
    const val TVERSKY_A = 0.5
    const val TVERSKY_B = 0.2
    const val TOPK = 3
    var CROSS_SITE = true

    fun workTokens(w: Work): List<String> {
        val toks = Norm.normTags(w.tags).toMutableList()
        for (t in Norm.titleTags(w.title)) if (t !in toks) toks.add(t)
        for (p in w.parodies) if (Norm.norm(p).isNotEmpty()) toks.add("原作:" + Norm.norm(Norm.normParody(p)))
        return toks
    }

    data class Person(val key: String, val weight: Double, val raw: String)

    fun workPeople(w: Work): List<Person> =
        w.authors.mapNotNull { a -> Norm.normPerson(a).takeIf { it.isNotEmpty() }?.let { Person(it, 1.0, a) } } +
            w.circles.mapNotNull { c -> Norm.normPerson(c).takeIf { it.isNotEmpty() }?.let { Person(it, 0.6, c) } }

    fun displayTag(t: String) = t.replace("原作:", "原作 ")
}

class Profile(val favorites: List<Work>, val favWeights: DoubleArray, val negatives: List<Work> = emptyList()) {
    val empty get() = favorites.isEmpty()

    companion object {
        fun build(bySite: Map<String, List<Work>>, negatives: List<Work>): Profile {
            val favs = ArrayList<Work>(); val ws = ArrayList<Double>()
            for ((_, works) in bySite) for ((rank, w) in works.withIndex()) {
                favs.add(w); ws.add(0.5 + 0.5 * exp(-rank / 200.0))
            }
            return Profile(favs, ws.toDoubleArray(), negatives)
        }
    }
}

/** 近义标签（替代电脑版的语义模型）：每个标签带上几个意思相近的标签，按分量算部分命中。 */
object NearTags {
    @Volatile var table: Map<String, List<Pair<String, Double>>> = emptyMap()

    fun load(json: String) {
        val m = HashMap<String, MutableList<Pair<String, Double>>>()
        fun link(a0: String, b0: String, w: Double) {
            val a = Norm.normTag(a0) ?: return; val b = Norm.normTag(b0) ?: return
            if (a == b) return
            m.getOrPut(a) { ArrayList() }.add(b to w)
            m.getOrPut(b) { ArrayList() }.add(a to w)
        }
        val root = Json.parse(json).obj()
        for (g in root["pairs"].arr()) { val l = g.arr(); if (l.size >= 3) link(l[0].str(), l[1].str(), l[2].dbl()) }
        for (g in root["groups"].arr()) {
            val l = g.arr(); if (l.size < 2) continue
            val w = l[0].dbl(); val tags = l[1].strList()
            for (i in tags.indices) for (j in i + 1 until tags.size) link(tags[i], tags[j], w)
        }
        table = m
    }

    val enabled get() = table.isNotEmpty()

    /** 标签 → 分量：自己 1，近义的按表里的分量（取最大）。 */
    fun expand(tokens: Collection<String>): Map<String, Double> {
        val out = HashMap<String, Double>()
        for (t in tokens) out[t] = 1.0
        for (t in tokens) for ((n, w) in table[t] ?: emptyList()) if ((out[n] ?: 0.0) < w) out[n] = w
        return out
    }
}

class FeatureBuilder(
    val p: Profile, background0: List<Work>, val semantic: Boolean = false, taste: TasteModel? = null,
    val hints: Map<String, List<String>> = emptyMap(),
) {
    val taste: TasteModel? = taste
    val vocab: Map<String, Int>
    val idf: DoubleArray
    private val favBin: Mat
    private val cross: DoubleArray
    private val favCross: BooleanArray
    private val negTok: Mat
    val people = HashMap<String, Double>()
    val peopleName = HashMap<String, String>()
    private val negPeople: Set<String>
    val tagPref = HashMap<String, Double>()
    private val favSem: Mat?
    private val negSem: Mat?

    init {
        val background = if (taste != null && taste.hasBackground) taste.background else background0
        val docs = background.map { Feat.workTokens(it) } + p.favorites.map { Feat.workTokens(it) }
        val df = HashMap<String, Int>()
        for (toks in docs) for (t in toks.toSet()) df.merge(t, 1, Int::plus)
        val sorted = df.keys.sorted()
        vocab = sorted.withIndex().associate { it.value to it.index }
        val n = maxOf(1, docs.size)
        idf = DoubleArray(sorted.size) { ln((n + 1.0) / (df.getValue(sorted[it]) + 1.0)) + 1 }
        favBin = binary(p.favorites.map { Feat.workTokens(it) })
        val jmDocs = background.map { Feat.workTokens(it) } + p.favorites.filter { it.site == "jm" }.map { Feat.workTokens(it) }
        val jmSet = jmDocs.flatten().toSet()
        cross = DoubleArray(sorted.size) { if (sorted[it] in jmSet) 1.0 else 0.0 }
        favCross = BooleanArray(p.favorites.size) { p.favorites[it].site != "jm" }
        negTok = tfidf(p.negatives.map { Feat.workTokens(it) })
        for ((w, wt) in p.favorites.zip(p.favWeights.toList())) for (pp in Feat.workPeople(w)) {
            people[pp.key] = (people[pp.key] ?: 0.0) + pp.weight * wt
            peopleName.putIfAbsent(pp.key, pp.raw)
        }
        negPeople = p.negatives.flatMap { w -> Feat.workPeople(w).map { it.key } }.toSet()
        for ((w, wt) in p.favorites.zip(p.favWeights.toList())) for (t in Feat.workTokens(w).toSet()) tagPref[t] = (tagPref[t] ?: 0.0) + wt
        favSem = if (semantic) semMatrix(p.favorites.map { Feat.workTokens(it) }) else null
        negSem = if (semantic && p.negatives.isNotEmpty()) semMatrix(p.negatives.map { Feat.workTokens(it) }) else null
    }

    private fun tfidf(docs: List<List<String>>): Mat {
        val m = Mat(docs.size, vocab.size)
        for ((i, toks) in docs.withIndex()) for (t in toks) vocab[t]?.let { m[i, it] = idf[it] }
        return m.l2rows()
    }

    private fun binary(docs: List<List<String>>): Mat {
        val m = Mat(docs.size, vocab.size)
        for ((i, toks) in docs.withIndex()) for (t in toks) vocab[t]?.let { m[i, it] = 1.0 }
        return m
    }

    /** 近义扩展后的 TF-IDF 向量（只用标签，不用原作），余弦相似度当“语义相似”。 */
    private fun semMatrix(docs: List<List<String>>): Mat {
        val m = Mat(docs.size, vocab.size)
        for ((i, toks) in docs.withIndex()) {
            for ((t, w) in NearTags.expand(toks.filter { !it.startsWith("原作:") })) vocab[t]?.let { m[i, it] = maxOf(m[i, it], w * idf[it]) }
        }
        return m.l2rows()
    }

    private fun tversky(c: Mat, f: Mat, v: DoubleArray, a: Double = Feat.TVERSKY_A, b: Double = Feat.TVERSKY_B): Mat {
        val out = Mat(c.rows, f.rows)
        if (c.rows == 0 || f.rows == 0) return out
        val cm = DoubleArray(c.rows) { i -> var s = 0.0; for (j in 0 until c.cols) s += c[i, j] * v[j]; s }
        val fm = DoubleArray(f.rows) { i -> var s = 0.0; for (j in 0 until f.cols) s += f[i, j] * v[j]; s }
        // 稀疏：每行只有十来个非零
        val cnz = Array(c.rows) { i -> (0 until c.cols).filter { c[i, it] != 0.0 }.toIntArray() }
        for (i in 0 until c.rows) for (k in 0 until f.rows) {
            var inter = 0.0
            for (j in cnz[i]) inter += c[i, j] * v[j] * f[k, j]
            val den = inter + a * (cm[i] - inter) + b * (fm[k] - inter)
            out[i, k] = if (den > 0) inter / den else 0.0
        }
        return out
    }

    private fun topkMean(sim: Mat, k0: Int = Feat.TOPK): DoubleArray {
        if (sim.cols == 0) return DoubleArray(sim.rows)
        val k = minOf(k0, sim.cols)
        return DoubleArray(sim.rows) { i -> sim.row(i).sortedDescending().take(k).average() }
    }

    private fun titleSim(cands: List<Work>, refs: List<Work>): Mat {
        if (cands.isEmpty() || refs.isEmpty()) return Mat(cands.size, refs.size)
        val texts = (cands + refs).map { w -> Norm.norm(Norm.parseTitle(w.title).core).ifEmpty { Norm.norm(w.title) } }
        val docs = texts.map { Tfidf.charWb(it, 2, 3) }
        if (docs.all { it.isEmpty() }) return Mat(cands.size, refs.size)
        val x = Tfidf.fit(docs)
        return Tfidf.sims(x.subList(0, cands.size), x.subList(cands.size, x.size))
    }

    private fun tokens(w: Work): List<String> {
        val toks = Feat.workTokens(w)
        val extra = if (!w.detailOk) hints[w.wid] else null
        return toks + (extra ?: emptyList()).filter { it !in toks }
    }

    class Built(val rows: List<Map<String, Double>>, val reasons: List<MutableMap<String, Any?>>)

    fun build(cands: List<Work>): Built {
        if (cands.isEmpty()) return Built(emptyList(), emptyList())
        val favs = p.favorites
        val cDocs = cands.map { tokens(it) }
        val cTok = tfidf(cDocs)
        val idf2 = DoubleArray(idf.size) { idf[it] * idf[it] }
        val tagSim: Mat
        if (favs.isEmpty()) tagSim = Mat(cands.size, 0) else {
            val cBin = binary(cDocs)
            tagSim = tversky(cBin, favBin, idf2)
            if (Feat.CROSS_SITE && favCross.any { it }) {
                val idx = favCross.indices.filter { favCross[it] }
                val fb = favBin.selectRows(idx)
                val shared = DoubleArray(vocab.size) { j -> var mx = 0.0; for (r in 0 until fb.rows) if (fb[r, j] > mx) mx = fb[r, j]; (if (mx > 0) 1.0 else 0.0) * cross[j] }
                val cb2 = cBin.copy(); for (i in 0 until cb2.rows) for (j in 0 until cb2.cols) cb2[i, j] *= shared[j]
                val fb2 = fb.copy(); for (i in 0 until fb2.rows) for (j in 0 until fb2.cols) fb2[i, j] *= shared[j]
                val s2 = tversky(cb2, fb2, idf2)
                for (i in 0 until cands.size) for ((r, col) in idx.withIndex()) tagSim[i, col] = s2[i, r]
            }
        }
        val tagFeat = topkMean(tagSim)
        val fitFeat = DoubleArray(cands.size) { i -> taste?.fit(cands[i], if (!cands[i].detailOk) hints[cands[i].wid] else null) ?: 0.0 }
        val tSim = titleSim(cands, favs)
        val titleFeat = topkMean(tSim, 1)

        var semSim = Mat(cands.size, favs.size)
        var semFeat = DoubleArray(cands.size)
        if (semantic && favs.isNotEmpty() && favSem != null) {
            val cs = semMatrix(cDocs)
            semSim = cs.mulT(favSem)
            for (i in 0 until semSim.a.size) semSim.a[i] = semSim.a[i].coerceIn(0.0, 1.0)
            semFeat = topkMean(semSim)
        }

        var negFeat = DoubleArray(cands.size)
        if (p.negatives.isNotEmpty()) {
            val nt0 = cTok.mulT(negTok)
            val nt = DoubleArray(cands.size) { i -> nt0.row(i).maxOrNull() ?: 0.0 }
            if (semantic && negSem != null) {
                val ns = semMatrix(cDocs).mulT(negSem)
                for (i in nt.indices) nt[i] = maxOf(nt[i], ns.row(i).maxOrNull() ?: 0.0)
            }
            negFeat = DoubleArray(cands.size) { i ->
                val same = if (Feat.workPeople(cands[i]).any { it.key in negPeople }) 1.0 else 0.0
                (0.7 * nt[i] + 0.3 * same).coerceIn(0.0, 1.0)
            }
        }
        val pops = cands.map { ln1p(maxOf(0.0, it.popularity)) }
        val span = (pops.maxOrNull() ?: 0.0) - (pops.minOrNull() ?: 0.0)
        val popFeat = pops.map { if (span > 0) (it - pops.min()) / span else 0.0 }

        val combined = Mat(cands.size, favs.size)
        for (i in 0 until cands.size) for (j in 0 until favs.size) combined[i, j] = tagSim[i, j] + 0.5 * tSim[i, j] + (if (semantic) semSim[i, j] else 0.0)

        val rows = ArrayList<Map<String, Double>>(); val reasons = ArrayList<MutableMap<String, Any?>>()
        for ((i, w) in cands.withIndex()) {
            var bestAff = 0.0; var bestName = ""; var bestKind = "作者"
            for (pp in Feat.workPeople(w)) {
                val v = (people[pp.key] ?: 0.0) * pp.weight
                if (v > bestAff) { bestAff = v; bestName = peopleName[pp.key] ?: pp.key; bestKind = if (pp.weight == 1.0) "作者" else "社团" }
            }
            val authorFeat = 1 - exp(-bestAff / 1.5)
            rows.add(linkedMapOf(
                "tag" to r4(tagFeat[i]), "fit" to r4(fitFeat[i]), "author" to r4(authorFeat), "title" to r4(titleFeat[i]),
                "semantic" to r4(semFeat[i]), "neg" to r4(negFeat[i]), "pop" to r4(popFeat[i]),
            ))
            val r = reason(w, i, combined, bestName, bestAff)
            r["author_kind"] = bestKind
            reasons.add(r)
        }
        return Built(rows, reasons)
    }

    private fun reason(w: Work, i: Int, combined: Mat, authorName: String, authorAff: Double): MutableMap<String, Any?> {
        val favs = p.favorites
        val nearest = ArrayList<Map<String, Any?>>()
        if (favs.isNotEmpty()) {
            val order = (0 until combined.cols).sortedByDescending { combined[i, it] }.take(Feat.TOPK)
            for (j in order) {
                if (combined[i, j] < 0.15) continue
                val f = favs[j]
                nearest.add(linkedMapOf("site" to f.site, "wid" to f.wid, "title" to Norm.parseTitle(f.title).core.ifEmpty { f.title },
                    "url" to f.url, "sim" to r3(combined[i, j])))
            }
        }
        val toks = tokens(w).toSet()
        val shared = if (taste != null) {
            toks.filter { (taste.pref[it] ?: 0.0) > 0.15 && it in tagPref }.sortedByDescending { taste.pref.getValue(it) }.take(4)
        } else {
            toks.filter { it in tagPref }.sortedByDescending { t -> vocab[t]?.let { tagPref.getValue(t) * idf[it] } ?: 0.0 }.take(4)
        }
        return linkedMapOf(
            "author" to (if (authorAff > 0) authorName else ""), "author_count" to r1(authorAff),
            "shared_tags" to shared.map { Feat.displayTag(it) }, "nearest" to nearest,
        )
    }
}

fun r4(x: Double) = Math.round(x * 10000.0) / 10000.0
fun r3(x: Double) = Math.round(x * 1000.0) / 1000.0
fun r2(x: Double) = Math.round(x * 100.0) / 100.0
fun r1(x: Double) = Math.round(x * 10.0) / 10.0
