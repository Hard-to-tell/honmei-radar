package honmei.algo

import honmei.*
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln

/** 固定打分基线 + 个人排序器（对应 ranker.py）。 */
object Ranker {
    val BASE_WEIGHTS = mapOf("tag" to 0.30, "fit" to 0.20, "author" to 0.30, "title" to 0.12, "semantic" to 0.0, "neg" to -0.35, "pop" to 0.08)
    // 近义标签打开时：拿出一点标签匹配的分量给“近义相似”（电脑版语义模型的位置）
    val SEMANTIC_WEIGHTS = mapOf("tag" to 0.24, "fit" to 0.20, "author" to 0.28, "title" to 0.10, "semantic" to 0.12, "neg" to -0.35, "pop" to 0.08)
    const val MIN_EACH = 3
    const val LOSS_MARGIN = 0.05
    const val SCALE = 6.0
    const val PRIOR_SD = 0.7

    fun weights(sem: Boolean) = if (sem) SEMANTIC_WEIGHTS else BASE_WEIGHTS

    fun baseScore(f: Map<String, Double>, sem: Boolean, w: Map<String, Double>? = null): Double {
        val ww = w ?: weights(sem)
        return Feat.FEATURES.sumOf { (ww[it] ?: 0.0) * (f[it] ?: 0.0) }
    }

    private fun logaddexp0(z: Double) = if (z > 0) z + ln(1 + exp(-z)) else ln(1 + exp(z))

    class MapFit(val bias: Double, val gain: Double, val delta: DoubleArray)

    /** 以固定权重为先验中心的逻辑回归：对数几率 = b + g·SCALE·base + d·x；g ≥ 0。用带投影的牛顿法解。 */
    fun fitMap(x: List<DoubleArray>, y: DoubleArray, base: DoubleArray): MapFit {
        val n = x.size; val k = x[0].size; val dim = k + 2
        val pos = maxOf(1, y.count { it == 1.0 }); val neg = maxOf(1, n - pos)
        val sw = DoubleArray(n) { if (y[it] == 1.0) n / (2.0 * pos) else n / (2.0 * neg) }
        val priorVar = DoubleArray(dim) { when (it) { 0 -> 4.0; 1 -> 0.25; else -> PRIOR_SD * PRIOR_SD } }
        val center = DoubleArray(dim) { if (it == 1) 1.0 else 0.0 }
        fun feat(i: Int) = DoubleArray(dim) { when (it) { 0 -> 1.0; 1 -> SCALE * base[i]; else -> x[i][it - 2] } }
        val feats = Array(n) { feat(it) }
        fun objective(t: DoubleArray): Double {
            var s = 0.0
            for (i in 0 until n) { val z = feats[i].indices.sumOf { feats[i][it] * t[it] }; s += sw[i] * (y[i] * logaddexp0(-z) + (1 - y[i]) * logaddexp0(z)) }
            for (j in 0 until dim) s += (t[j] - center[j]) * (t[j] - center[j]) / (2 * priorVar[j])
            return s
        }
        var t = center.copyOf()
        var f = objective(t)
        for (iter in 0 until 100) {
            val g = DoubleArray(dim); val h = Mat(dim, dim)
            for (i in 0 until n) {
                val z = feats[i].indices.sumOf { feats[i][it] * t[it] }
                val p = 1 / (1 + exp(-z)); val r = sw[i] * (p - y[i]); val c = sw[i] * p * (1 - p)
                for (a in 0 until dim) { g[a] += r * feats[i][a]; for (b in 0 until dim) h[a, b] += c * feats[i][a] * feats[i][b] }
            }
            for (j in 0 until dim) { g[j] += (t[j] - center[j]) / priorVar[j]; h[j, j] += 1 / priorVar[j] }
            // g（下标 1）在边界 0 上且梯度朝外：这一维固定
            val free = (0 until dim).filter { !(it == 1 && t[1] <= 0.0 && g[1] > 0) }
            val step = solve(h, g, free)
            var lr = 1.0; var improved = false
            while (lr > 1e-6) {
                val nt = t.copyOf()
                for (j in free) nt[j] -= lr * step[j]
                if (nt[1] < 0) nt[1] = 0.0
                val nf = objective(nt)
                if (nf <= f - 1e-12) { t = nt; improved = abs(f - nf) > 1e-10; f = nf; break }
                lr /= 2
            }
            if (!improved) break
        }
        return MapFit(t[0], t[1], t.copyOfRange(2, dim))
    }

    /** 解 H[free,free]·s = g[free]（高斯消元）。 */
    private fun solve(h: Mat, g: DoubleArray, free: List<Int>): DoubleArray {
        val m = free.size
        val a = Array(m) { r -> DoubleArray(m + 1) { c -> if (c < m) h[free[r], free[c]] else g[free[r]] } }
        for (c in 0 until m) {
            var piv = c
            for (r in c + 1 until m) if (abs(a[r][c]) > abs(a[piv][c])) piv = r
            val tmp = a[c]; a[c] = a[piv]; a[piv] = tmp
            if (abs(a[c][c]) < 1e-12) continue
            for (r in 0 until m) if (r != c) { val fct = a[r][c] / a[c][c]; for (cc in c..m) a[r][cc] -= fct * a[c][cc] }
        }
        val out = DoubleArray(h.rows)
        for (r in 0 until m) out[free[r]] = if (abs(a[r][r]) < 1e-12) 0.0 else a[r][m] / a[r][r]
        return out
    }

    private fun vec(f: Map<String, Any?>) = DoubleArray(Feat.FEATURES.size) { f[Feat.FEATURES[it]].dbl() }

    private fun auc(y: DoubleArray, s: DoubleArray): Double? {
        val pos = s.indices.filter { y[it] == 1.0 }.map { s[it] }; val neg = s.indices.filter { y[it] == 0.0 }.map { s[it] }
        if (pos.isEmpty() || neg.isEmpty()) return null
        var wins = 0.0
        for (p in pos) for (q in neg) wins += if (p > q) 1.0 else if (p == q) 0.5 else 0.0
        return wins / (pos.size * neg.size)
    }

    class TRow(val batchId: Long, val x: DoubleArray, val y: Int, val authors: Set<String>)

    fun trainingRows(db: DB): List<TRow> {
        val rows = db.q(
            "SELECT i.batch_id, i.features, i.site, i.wid, f.label FROM feedback f JOIN batch_items i ON i.site=f.site AND i.wid=f.wid " +
                "AND i.batch_id=f.batch_id WHERE f.label IN ('want', 'seen_like', 'skip', 'dislike') ORDER BY i.batch_id",
        )
        val out = ArrayList<TRow>()
        for (r in rows) {
            val w = db.getWork(r["site"].str(), r["wid"].str())
            val authors = (w?.authors ?: emptyList()).map { Norm.normPerson(it) }.toSet().ifEmpty { setOf(r["wid"].str()) }
            out.add(TRow(r["batch_id"].long(), vec(Json.parse(r["features"].str()).obj()), if (r["label"].str() in setOf("want", "seen_like")) 1 else 0, authors))
        }
        val seen = rows.map { it["site"].str() to it["wid"].str() }.toSet()
        val cal = db.q(
            "SELECT c.site, c.wid, c.level, MAX(i.batch_id) batch_id, i.features FROM calibration c JOIN batch_items i ON i.site=c.site AND i.wid=c.wid " +
                "WHERE c.source='rec' AND c.level IN (1, 3, 4) GROUP BY c.site, c.wid",
        )
        for (r in cal) {
            if ((r["site"].str() to r["wid"].str()) in seen) continue
            val w = db.getWork(r["site"].str(), r["wid"].str())
            val authors = (w?.authors ?: emptyList()).map { Norm.normPerson(it) }.toSet().ifEmpty { setOf(r["wid"].str()) }
            out.add(TRow(r["batch_id"].long(), vec(Json.parse(r["features"].str()).obj()), if (r["level"].int() >= 3) 1 else 0, authors))
        }
        return out.sortedBy { it.batchId }
    }

    fun train(db: DB, sem: Boolean): MutableMap<String, Any?> {
        val rows = trainingRows(db)
        val nPos = rows.count { it.y == 1 }; val nNeg = rows.size - nPos
        val batches = rows.map { it.batchId }.toSortedSet().toList()
        val state = linkedMapOf<String, Any?>("mode" to "baseline", "alpha" to 0.0, "n_pos" to nPos, "n_neg" to nNeg,
            "n_authors" to rows.flatMap { it.authors }.toSet().size, "n_batches" to batches.size, "updated_at" to now(),
            "weights" to null, "auc_base" to null, "auc_model" to null, "semantic" to sem)
        if (nPos == 0 || nNeg == 0) {
            state["message"] = "还没同时收到“心动”和“略过”，先按固定打分排。"
            db.kvSet("ranker", state); return state
        }
        if (nPos < MIN_EACH || nNeg < MIN_EACH) {
            state["message"] = "已有心动 $nPos、略过 $nNeg，各满 $MIN_EACH 条后开始按你的反馈微调；你点过的标签已经在影响“口味契合”。"
            db.kvSet("ranker", state); return state
        }
        val x = rows.map { it.x }; val y = DoubleArray(rows.size) { rows[it].y.toDouble() }
        val base = DoubleArray(rows.size) { i -> baseScore(Feat.FEATURES.zip(x[i].toList()).toMap(), sem) }
        if (batches.size >= 3) {
            val cut = batches[maxOf(1, (batches.size * 2 / 3.0).toInt()) - 1]
            val tr = rows.indices.filter { rows[it].batchId <= cut }; val te = rows.indices.filter { rows[it].batchId > cut }
            if (tr.map { y[it] }.toSet().size == 2 && te.map { y[it] }.toSet().size == 2) {
                val m = fitMap(tr.map { x[it] }, DoubleArray(tr.size) { y[tr[it]] }, DoubleArray(tr.size) { base[tr[it]] })
                val ms = DoubleArray(te.size) { j -> val i = te[j]; m.gain * SCALE * base[i] + x[i].indices.sumOf { m.delta[it] * x[i][it] } }
                state["auc_model"] = auc(DoubleArray(te.size) { y[te[it]] }, ms)
                state["auc_base"] = auc(DoubleArray(te.size) { y[te[it]] }, DoubleArray(te.size) { base[te[it]] })
            }
        }
        val m = fitMap(x, y, base)
        val bw = weights(sem)
        state["weights"] = Feat.FEATURES.withIndex().associate { (j, f) -> f to r4(m.gain * SCALE * (bw[f] ?: 0.0) + m.delta[j]) }
        state["bias"] = r4(m.bias)
        val am = state["auc_model"] as Double?; val ab = state["auc_base"] as Double?
        if (am != null && ab != null && am < ab - LOSS_MARGIN) {
            state["mode"] = "shadow"
            state["message"] = "按你反馈微调的权重在最近几卷上不如固定打分（AUC ${"%.2f".format(am)} 对 ${"%.2f".format(ab)}），暂时退回固定打分，继续观察。"
        } else {
            state["mode"] = "personal"; state["alpha"] = 1.0
            val extra = if (am != null) "，最近几卷检验 AUC ${"%.2f".format(am)}（固定打分 ${"%.2f".format(ab)}）" else ""
            state["message"] = "已按你的 $nPos 条心动、$nNeg 条略过微调排序$extra。"
        }
        db.kvSet("ranker", state)
        return state
    }

    fun modelScore(state: Map<String, Any?>, f: Map<String, Double>): Double? {
        val w = state["weights"].obj().takeIf { it.isNotEmpty() } ?: return null
        return state["bias"].dbl() + Feat.FEATURES.sumOf { w[it].dbl() * (f[it] ?: 0.0) }
    }

    fun finalScores(feats: List<Map<String, Double>>, sem: Boolean, state: Map<String, Any?>?): Pair<MutableList<Double>, String> {
        val base = feats.map { baseScore(it, sem) }.toMutableList()
        val engine = if (sem) "baseline+semantic" else "baseline"
        if (state == null || state["mode"] != "personal" || state["weights"].obj().isEmpty()) return base to engine
        return feats.map { 1 / (1 + exp(-(modelScore(state, it) ?: 0.0))) }.toMutableList() to "$engine+personal"
    }

    fun describeWeights(state: Map<String, Any?>): String {
        val w = state["weights"].obj().takeIf { it.isNotEmpty() } ?: return ""
        val names = mapOf("tag" to "标签", "fit" to "口味契合", "author" to "作者", "title" to "标题", "semantic" to "近义", "neg" to "负例", "pop" to "热度")
        return w.entries.sortedByDescending { abs(it.value.dbl()) }.filter { abs(it.value.dbl()) > 1e-9 }
            .joinToString("，") { "${names[it.key]} ${"%+.2f".format(it.value.dbl())}" }
    }
}
