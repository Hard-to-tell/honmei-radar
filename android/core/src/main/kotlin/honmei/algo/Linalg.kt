package honmei.algo

import kotlin.math.abs
import kotlin.math.sqrt

/** 稠密矩阵（行主序）。规模都很小（几百 × 几百），不需要第三方数学库。 */
class Mat(val rows: Int, val cols: Int, val a: DoubleArray = DoubleArray(rows * cols)) {
    operator fun get(i: Int, j: Int) = a[i * cols + j]
    operator fun set(i: Int, j: Int, v: Double) { a[i * cols + j] = v }
    fun row(i: Int): DoubleArray = a.copyOfRange(i * cols, (i + 1) * cols)

    fun t(): Mat { val m = Mat(cols, rows); for (i in 0 until rows) for (j in 0 until cols) m[j, i] = this[i, j]; return m }

    /** this · other */
    fun mul(o: Mat): Mat {
        require(cols == o.rows)
        val m = Mat(rows, o.cols)
        for (i in 0 until rows) {
            val ri = i * cols
            val mi = i * o.cols
            for (k in 0 until cols) {
                val v = a[ri + k]
                if (v == 0.0) continue
                val ok = k * o.cols
                for (j in 0 until o.cols) m.a[mi + j] += v * o.a[ok + j]
            }
        }
        return m
    }

    /** this · otherᵀ */
    fun mulT(o: Mat): Mat {
        require(cols == o.cols)
        val m = Mat(rows, o.rows)
        for (i in 0 until rows) for (j in 0 until o.rows) {
            var s = 0.0
            val ri = i * cols; val rj = j * o.cols
            for (k in 0 until cols) s += a[ri + k] * o.a[rj + k]
            m[i, j] = s
        }
        return m
    }

    fun selectRows(idx: List<Int>): Mat { val m = Mat(idx.size, cols); for ((r, i) in idx.withIndex()) System.arraycopy(a, i * cols, m.a, r * cols, cols); return m }
    fun copy() = Mat(rows, cols, a.copyOf())
    fun mean(): Double = if (a.isEmpty()) 0.0 else a.sum() / a.size
    fun rowSums() = DoubleArray(rows) { i -> var s = 0.0; for (j in 0 until cols) s += this[i, j]; s }
    fun rowNorms() = DoubleArray(rows) { i -> var s = 0.0; for (j in 0 until cols) s += this[i, j] * this[i, j]; sqrt(s) }

    /** 每行除以 L2 范数（全零行保持为 0）。 */
    fun l2rows(eps: Double = 0.0, zeroToOne: Boolean = true): Mat {
        val m = copy(); val n = rowNorms()
        for (i in 0 until rows) {
            val d = if (eps > 0) n[i] + eps else if (n[i] == 0.0 && zeroToOne) 1.0 else n[i]
            for (j in 0 until cols) m[i, j] = m[i, j] / d
        }
        return m
    }
}

object Linalg {
    /** 对称矩阵的特征分解（Jacobi 旋转）。返回 (特征值, 特征向量按列)。 */
    fun eigSym(s: Mat, maxSweeps: Int = 60): Pair<DoubleArray, Mat> {
        val n = s.rows
        val a = s.copy()
        val v = Mat(n, n).also { for (i in 0 until n) it[i, i] = 1.0 }
        for (sweep in 0 until maxSweeps) {
            var off = 0.0
            for (p in 0 until n) for (q in p + 1 until n) off += a[p, q] * a[p, q]
            if (off < 1e-22) break
            for (p in 0 until n) for (q in p + 1 until n) {
                val apq = a[p, q]
                if (abs(apq) < 1e-15) continue
                val app = a[p, p]; val aqq = a[q, q]
                val theta = (aqq - app) / (2 * apq)
                val t = (if (theta >= 0) 1.0 else -1.0) / (abs(theta) + sqrt(theta * theta + 1))
                val c = 1 / sqrt(t * t + 1); val sn = t * c
                for (k in 0 until n) {
                    val akp = a[k, p]; val akq = a[k, q]
                    a[k, p] = c * akp - sn * akq; a[k, q] = sn * akp + c * akq
                }
                for (k in 0 until n) {
                    val apk = a[p, k]; val aqk = a[q, k]
                    a[p, k] = c * apk - sn * aqk; a[q, k] = sn * apk + c * aqk
                }
                for (k in 0 until n) {
                    val vkp = v[k, p]; val vkq = v[k, q]
                    v[k, p] = c * vkp - sn * vkq; v[k, q] = sn * vkp + c * vkq
                }
            }
        }
        return DoubleArray(n) { a[it, it] } to v
    }

    /** 截断 SVD：X ≈ U·diag(S)·Vᵀ，取前 k 个。通过 XᵀX（或 XXᵀ，取小的一边）的特征分解。 */
    fun svd(x: Mat, k: Int): Triple<Mat, DoubleArray, Mat> {
        val small = x.cols <= x.rows
        val g = if (small) x.t().mul(x) else x.mulT(x)
        val (ev, vecs) = eigSym(g)
        val order = ev.indices.sortedByDescending { ev[it] }.take(k)
        val s = DoubleArray(k) { sqrt(maxOf(ev[order[it]], 0.0)) }
        val u = Mat(x.rows, k); val vt = Mat(k, x.cols)
        if (small) {
            for ((c, idx) in order.withIndex()) {
                for (j in 0 until x.cols) vt[c, j] = vecs[j, idx]
                for (i in 0 until x.rows) {
                    var t = 0.0
                    for (j in 0 until x.cols) t += x[i, j] * vt[c, j]
                    u[i, c] = if (s[c] > 1e-12) t / s[c] else 0.0
                }
            }
        } else {
            for ((c, idx) in order.withIndex()) {
                for (i in 0 until x.rows) u[i, c] = vecs[i, idx]
                for (j in 0 until x.cols) {
                    var t = 0.0
                    for (i in 0 until x.rows) t += x[i, j] * u[i, c]
                    vt[c, j] = if (s[c] > 1e-12) t / s[c] else 0.0
                }
            }
        }
        return Triple(u, s, vt)
    }
}

/** TF-IDF（照 sklearn 的默认：smooth_idf、l2 归一化），稀疏行。 */
object Tfidf {
    class Sparse(val idx: IntArray, val v: DoubleArray) {
        fun dot(o: Sparse): Double {
            var i = 0; var j = 0; var s = 0.0
            while (i < idx.size && j < o.idx.size) {
                when {
                    idx[i] == o.idx[j] -> { s += v[i] * o.v[j]; i++; j++ }
                    idx[i] < o.idx[j] -> i++
                    else -> j++
                }
            }
            return s
        }
    }

    /** docs: 每篇是词的列表（含重复）。返回每篇的稀疏向量。 */
    fun fit(docs: List<List<String>>, sublinear: Boolean = false): List<Sparse> {
        val vocab = HashMap<String, Int>()
        for (t in docs.flatten().toSortedSet()) vocab[t] = vocab.size
        val n = docs.size
        val df = IntArray(vocab.size)
        for (d in docs) for (t in d.toSet()) df[vocab.getValue(t)]++
        val idf = DoubleArray(vocab.size) { Math.log((1.0 + n) / (1.0 + df[it])) + 1.0 }
        return docs.map { d ->
            val cnt = java.util.TreeMap<Int, Int>()
            for (t in d) cnt.merge(vocab.getValue(t), 1, Int::plus)
            val idx = cnt.keys.toIntArray()
            val v = DoubleArray(idx.size) { k -> val c = cnt.getValue(idx[k]); (if (sublinear) 1 + Math.log(c.toDouble()) else c.toDouble()) * idf[idx[k]] }
            val norm = Math.sqrt(v.sumOf { it * it })
            if (norm > 0) for (k in v.indices) v[k] /= norm
            Sparse(idx, v)
        }
    }

    /** a × bᵀ 的相似度矩阵。 */
    fun sims(a: List<Sparse>, b: List<Sparse>): Mat {
        val m = Mat(a.size, b.size)
        // 倒排：词 → (b 的行, 值)
        val inv = HashMap<Int, MutableList<Pair<Int, Double>>>()
        for ((r, s) in b.withIndex()) for (k in s.idx.indices) inv.getOrPut(s.idx[k]) { ArrayList() }.add(r to s.v[k])
        for ((i, s) in a.withIndex()) for (k in s.idx.indices) inv[s.idx[k]]?.forEach { (r, v) -> m[i, r] += s.v[k] * v }
        return m
    }

    /** sklearn analyzer="char_wb" 的切法（小写）。 */
    fun charWb(text: String, minN: Int, maxN: Int): List<String> {
        val out = ArrayList<String>()
        for (word in text.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }) {
            val cps = " $word ".codePoints().toArray()
            val wl = cps.size
            for (n in minN..maxN) {
                var off = 0
                out.add(String(cps, off, minOf(n, wl - off)))
                while (off + n < wl) { off++; out.add(String(cps, off, minOf(n, wl - off))) }
                if (off == 0) break
            }
        }
        return out
    }
}
