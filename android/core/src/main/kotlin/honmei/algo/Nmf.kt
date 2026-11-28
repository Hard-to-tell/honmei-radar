package honmei.algo

import kotlin.math.abs
import kotlin.math.sqrt

/** NMF（照 sklearn：init="nndsvda"，坐标下降 solver="cd"，Frobenius 损失，tol=1e-4）。 */
class Nmf(val k: Int, val maxIter: Int = 600, val tol: Double = 1e-4) {
    lateinit var components: Mat   // H：k × 特征数

    fun fitTransform(x: Mat): Mat {
        val (w, h) = initNndsvda(x)
        fitCd(x, w, h, updateH = true)
        components = h
        return w
    }

    fun transform(x: Mat): Mat {
        val avg = sqrt(x.mean() / k)
        val w = Mat(x.rows, k).also { java.util.Arrays.fill(it.a, avg) }
        fitCd(x, w, components, updateH = false)
        return w
    }

    private fun initNndsvda(x: Mat): Pair<Mat, Mat> {
        val (u, s, v) = Linalg.svd(x, k)
        val w = Mat(x.rows, k); val h = Mat(k, x.cols)
        for (i in 0 until x.rows) w[i, 0] = sqrt(s[0]) * abs(u[i, 0])
        for (j in 0 until x.cols) h[0, j] = sqrt(s[0]) * abs(v[0, j])
        for (c in 1 until k) {
            val xp = DoubleArray(x.rows) { maxOf(u[it, c], 0.0) }; val xn = DoubleArray(x.rows) { abs(minOf(u[it, c], 0.0)) }
            val yp = DoubleArray(x.cols) { maxOf(v[c, it], 0.0) }; val yn = DoubleArray(x.cols) { abs(minOf(v[c, it], 0.0)) }
            val xpn = norm(xp); val ypn = norm(yp); val xnn = norm(xn); val ynn = norm(yn)
            val mp = xpn * ypn; val mn = xnn * ynn
            val (uu, vv, sigma) = if (mp > mn) Triple(xp.map { it / xpn }, yp.map { it / ypn }, mp) else Triple(xn.map { it / xnn }, yn.map { it / ynn }, mn)
            val lbd = sqrt(s[c] * sigma)
            for (i in 0 until x.rows) w[i, c] = lbd * uu[i].let { if (it.isNaN()) 0.0 else it }
            for (j in 0 until x.cols) h[c, j] = lbd * vv[j].let { if (it.isNaN()) 0.0 else it }
        }
        val eps = 1e-6
        val avg = x.mean()
        for (i in w.a.indices) { if (w.a[i] < eps) w.a[i] = 0.0; if (w.a[i] == 0.0) w.a[i] = avg }
        for (i in h.a.indices) { if (h.a[i] < eps) h.a[i] = 0.0; if (h.a[i] == 0.0) h.a[i] = avg }
        return w to h
    }

    private fun norm(a: DoubleArray) = sqrt(a.sumOf { it * it })

    private fun fitCd(x: Mat, w: Mat, h: Mat, updateH: Boolean) {
        val xt = if (updateH) x.t() else null
        var violationInit = 0.0
        for (iter in 1..maxIter) {
            var violation = 0.0
            val ht = h.t()                                   // 特征数 × k
            violation += update(x, w, ht)
            if (updateH) {
                violation += update(xt!!, ht, w)
                // ht 改过了，写回 h
                for (i in 0 until h.rows) for (j in 0 until h.cols) h[i, j] = ht[j, i]
            }
            if (iter == 1) violationInit = violation
            if (violationInit == 0.0) break
            if (violation / violationInit <= tol) break
        }
    }

    /** sklearn _update_cdnmf_fast：按列逐个更新 W。返回投影梯度的绝对值之和。 */
    private fun update(x: Mat, w: Mat, ht: Mat): Double {
        val nc = ht.cols
        val hht = ht.t().mul(ht)        // k × k
        val xht = x.mul(ht)             // n × k
        var violation = 0.0
        for (t in 0 until nc) {
            val hess = hht[t, t]
            for (i in 0 until w.rows) {
                var grad = -xht[i, t]
                for (r in 0 until nc) grad += hht[t, r] * w[i, r]
                val pg = if (w[i, t] == 0.0) minOf(0.0, grad) else grad
                violation += abs(pg)
                if (hess != 0.0) w[i, t] = maxOf(w[i, t] - grad / hess, 0.0)
            }
        }
        return violation
    }
}
