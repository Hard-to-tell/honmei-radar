package honmei.algo

import honmei.*

/** 解释某一本为什么被推荐（只用本地证据，对应 explain.py 的本地部分）。 */
object Explain {
    val FEATURE_NAMES = mapOf("tag" to "标签匹配", "fit" to "口味契合", "author" to "作者亲和", "title" to "标题相似",
        "semantic" to "近义标签", "neg" to "像你略过的作品", "pop" to "站内热度")

    fun evidence(db: DB, bid: Long, pos: Int): Map<String, Any?>? {
        val it = db.batchItems(bid).firstOrNull { it["pos"].int() == pos } ?: return null
        val w = db.getWork(it["site"].str(), it["wid"].str())
        val meta = db.batchMeta(bid) ?: emptyMap()
        val reason = it["reason"].obj()
        val nearest = reason["nearest"].arr().mapNotNull { n0 ->
            val n = n0.obj()
            db.getWork(n["site"].str(), n["wid"].str())?.let { f ->
                mapOf("title" to f.title, "authors" to f.authors, "tags" to f.tags.take(12), "parodies" to f.parodies,
                    "site" to if (f.site == "jm") "禁漫" else "哔咔", "similarity" to n["sim"])
            }
        }
        return mapOf(
            "batch_id" to bid, "vol" to meta["vol"], "pos" to pos, "engine" to meta["engine"],
            "candidate" to mapOf("title" to (w?.title ?: it["wid"]), "authors" to (w?.authors ?: emptyList<String>()),
                "tags" to (w?.tags ?: emptyList<String>()), "parodies" to (w?.parodies ?: emptyList<String>()),
                "description" to (w?.description?.take(300) ?: ""), "url" to (w?.url ?: "")),
            "route" to (reason["route_name"] ?: it["route"]), "features" to it["features"], "score" to it["score"],
            "reason" to reason["text"].str(), "shared_tags" to reason["shared_tags"].strList(), "author" to reason["author"].str(),
            "nearest_favorites" to nearest,
        )
    }

    fun local(ev: Map<String, Any?>): String {
        val sem = ev["engine"].str().contains("semantic")
        val weights = Ranker.weights(sem)
        val f = ev["features"].obj()
        val contrib = Feat.FEATURES.filter { (weights[it] ?: 0.0) != 0.0 }
            .map { k -> k to weights.getValue(k) * (f[k].dbl() - if (k == "fit") 0.5 else 0.0) }
            .sortedByDescending { kotlin.math.abs(it.second) }
        val cand = ev["candidate"].obj()
        val lines = arrayListOf("Vol.${ev["vol"].str().ifEmpty { ev["batch_id"].str() }} · No.${ev["pos"]}　${cand["title"].str()}",
            "来路：${ev["route"].str()}。${ev["reason"].str()}")
        val top = contrib.take(4).filter { kotlin.math.abs(it.second) >= 0.01 }.map { "${FEATURE_NAMES[it.first]} ${"%+.2f".format(it.second)}" }
        if (top.isNotEmpty()) lines.add("分数从哪来（固定权重）：" + top.joinToString("，"))
        val near = ev["nearest_favorites"].arr()
        if (near.isNotEmpty()) {
            lines.add("书架上离它最近的几本：")
            for (n0 in near) { val n = n0.obj(); lines.add("  · [${n["site"].str()}] ${n["title"].str()}（作者 ${n["authors"].strList().joinToString("、").ifEmpty { "未知" }}）") }
        }
        lines.add("（依据只来自标题、作者和标签这些文字，画风合不合，还得你自己翻一眼。）")
        return lines.joinToString("\n")
    }
}
