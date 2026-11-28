package honmei.algo

import honmei.*

/** 口味谱的统计（对应 insights.py）。只用收藏的标题、作者、标签、原作和收藏先后，全在手机里算。 */
object Insights {
    val DIMENSIONS: List<Pair<String, List<String>>> = listOf(
        "关系与情节" to listOf("纯爱", "恋爱", "ntr", "出轨", "后宫", "乱伦", "近亲", "催眠", "调教", "强暴", "凌辱", "奸",
            "百合", "耽美", "师生", "青梅", "初恋", "交换", "援交", "卖春", "偷窥", "露出", "支配", "榨精",
            "逆推", "堕落", "恶堕", "洗脑", "常识改变", "时间停止", "睡眠", "痴汉", "女性支配", "男性受"),
        "角色身份" to listOf("妹", "姐", "人妻", "熟女", "母", "学生", "jk", "女高中生", "老师", "女仆", "护士", "辣妹", "巫女",
            "修女", "偶像", "大小姐", "魅魔", "精灵", "兽耳", "猫娘", "非人", "怪物", "伪娘", "扶他", "萝莉",
            "正太", "学妹", "学姐", "上司", "邻居", "青梅竹马", "处女", "小恶魔", "无口", "傲娇", "痴女"),
        "身体特征" to listOf("巨乳", "贫乳", "爆乳", "大乳晕", "乳", "肉感", "肌肉", "褐", "黑皮", "多毛", "腋", "足", "腿", "臀",
            "孕", "怀孕", "眼镜", "短发", "长发", "双马尾", "马尾", "黑长直", "单马尾", "痣"),
        "服装" to listOf("校服", "制服", "过膝袜", "长筒袜", "连裤", "丝袜", "泳装", "内衣", "情趣", "兔女郎", "女仆装", "体操服",
            "旗袍", "和服", "比基尼", "围裙", "睡衣", "运动服", "cosplay", "项圈"),
        "画面与表现" to listOf("横切面", "透视", "阿黑颜", "阿嘿颜", "蹦坏脸", "颜射", "口爆", "潮吹", "失禁", "汗", "精液", "淫纹",
            "无修正", "全彩", "黑白", "ai"),
        "篇幅与形式" to listOf("长篇", "短篇", "单行本", "剧情", "同人", "cg", "杂志", "合集", "韩漫", "3d", "连载", "完结"),
    )
    val AXES: Map<String, List<String>> = linkedMapOf(
        "纯爱" to listOf("纯爱", "恋爱", "初恋", "接吻", "甜", "后宫闪光", "青梅"),
        "禁忌" to listOf("ntr", "出轨", "乱伦", "近亲", "寝取", "交换", "援交"),
        "被支配" to listOf("女性支配", "榨精", "逆推", "痴女", "魅魔", "男性受", "骑乘", "骑乗", "小恶魔"),
        "支配" to listOf("调教", "催眠", "强暴", "凌辱", "洗脑", "常识改变", "时间停止", "束缚", "项圈", "堕落", "恶堕"),
        "剧情" to listOf("剧情", "长篇", "单行本", "连载"),
        "丰满" to listOf("巨乳", "爆乳", "大乳晕", "肉感", "乳交"),
        "娇小" to listOf("贫乳", "萝莉", "小只"),
        "制服" to listOf("校服", "制服", "女高中生", "jk", "学生", "体操服", "女仆"),
        "腿控" to listOf("过膝袜", "长筒袜", "连裤", "丝袜", "足", "腿"),
        "重口" to listOf("阿黑颜", "阿嘿颜", "蹦坏脸", "失禁", "触手", "怪物", "重口", "猎奇", "肛"),
    )
    /** 性学侧写：参考性学里常见的分法，用标签粗估，和站内平均比。 */
    val SEXO: List<Triple<String, String, List<String>>> = listOf(
        Triple("情感联结", "Romantic", listOf("纯爱", "恋爱", "初恋", "接吻", "亲吻", "甜", "青梅", "女友", "夫妻", "告白", "后宫闪光", "爱情")),
        Triple("感官刺激", "Sensory", listOf("潮吹", "横切面", "阿嘿颜", "阿黑颜", "中出", "内射", "颜射", "口爆", "精液", "失禁", "淫纹", "高潮")),
        Triple("SM / 权力交换", "BDSM · D/s", listOf("sm", "调教", "束缚", "拘束", "捆绑", "绳", "项圈", "鞭", "滴蜡", "窒息", "虐", "女性支配", "女性主导", "榨精", "催眠", "洗脑", "凌辱", "堕落", "恶堕", "痴女", "逆推", "奴隶", "常识改变")),
        Triple("恋物", "Fetish", listOf("丝袜", "长筒袜", "连裤", "足", "腋", "眼镜", "制服", "校服", "女仆", "泳装", "兔女郎", "体操服", "内衣", "旗袍", "乳头")),
        Triple("禁忌越界", "Taboo", listOf("ntr", "出轨", "乱伦", "近亲", "寝取", "人妻", "援交", "师生", "交换", "外遇")),
        Triple("窥视与暴露", "Voyeur / Exhib", listOf("露出", "偷窥", "野外", "公众", "电车", "痴汉", "偷拍", "直播", "羞耻")),
        Triple("多人", "Group", listOf("3p", "多p", "4p", "轮奸", "乱交", "群交", "后宫")),
        Triple("非人幻想", "Fantasy", listOf("触手", "怪物", "魅魔", "人外", "兽耳", "异种", "精灵", "机械", "史莱姆", "异世界")),
    )
    val NOISY_AUTHORS = setOf("3d", "急求", "多作者", "よろず", "anthology", "アンソロジー", "未知", "unknown", "ai", "pixiv")
    const val HIDDEN_KEY = "report:hidden_tags"
    private val EVENT = Regex("^(c\\d{2,3}|comic.*|\\d+.*)$")

    private fun match(tag: String, keys: List<String>) = keys.any { it in tag }
    fun dimensionOf(tag: String) = DIMENSIONS.firstOrNull { match(tag, it.second) }?.first

    class Fav(val w: Work, val rank: Int)

    private fun favorites(db: DB): List<Fav> =
        db.q("SELECT w.*, c.rank FROM collections c JOIN works w ON w.site=c.site AND w.wid=c.wid ORDER BY c.site, c.rank")
            .map { Fav(db.rowToWork(it), it["rank"].int()) }

    fun brief(w: Work): MutableMap<String, Any?> = linkedMapOf("site" to w.site, "wid" to w.wid,
        "title" to Norm.parseTitle(w.title).core.ifEmpty { w.title }, "authors" to w.authors.take(2),
        "cover" to "/api/cover/${w.site}/${w.wid}", "url" to w.url)

    fun hiddenTags(db: DB): List<String> = db.kvGet(HIDDEN_KEY).strList()

    fun setHidden(db: DB, tag: String, hidden: Boolean): List<String> {
        val t = if (tag.isEmpty()) "" else (Norm.normTags(listOf(tag)).firstOrNull() ?: Norm.norm(tag))
        if (t.isEmpty()) return hiddenTags(db)
        val cur = hiddenTags(db).filter { it != t } + (if (hidden) listOf(t) else emptyList())
        db.kvSet(HIDDEN_KEY, cur)
        return cur
    }

    private fun <K> top(m: Map<K, Int>, n: Int) = m.entries.sortedByDescending { it.value }.take(n)
    private fun pct3(x: Double) = r3(x)

    fun summary(db: DB): Map<String, Any?> {
        val favs = favorites(db)
        if (favs.isEmpty()) return mapOf("empty" to true)
        val hidden = hiddenTags(db).toSet()
        val n = favs.size
        val perSite = favs.groupingBy { it.w.site }.eachCount()
        val tagSets = ArrayList<List<String>>()
        val tagCount = HashMap<String, Int>(); val authorCount = HashMap<String, Int>(); val authorName = HashMap<String, String>()
        val authorWorks = HashMap<String, MutableList<Work>>(); val parodyCount = HashMap<String, Int>(); val parodyName = HashMap<String, String>()
        val groupCount = HashMap<String, Int>()
        var color = 0; var long = 0
        for (f in favs) {
            val w = f.w
            val ts = Norm.normTags(w.tags).filter { it !in hidden }.distinct()
            tagSets.add(ts)
            for (t in ts) tagCount.merge(t, 1, Int::plus)
            for (a in w.authors.toSet()) {
                val k = Norm.normPerson(a)
                if (k.isNotEmpty() && k !in NOISY_AUTHORS && k.length > 1) {
                    authorCount.merge(k, 1, Int::plus); authorName.putIfAbsent(k, a); authorWorks.getOrPut(k) { ArrayList() }.add(w)
                }
            }
            for (p in w.parodies.map { Norm.normParody(it) }.toSet()) {
                val k = Norm.norm(p)
                if (k.isNotEmpty() && "comic" !in k && "快乐天" !in k && k != "ai") { parodyCount.merge(k, 1, Int::plus); parodyName.putIfAbsent(k, p) }
            }
            for (g in w.groups.take(1)) groupCount.merge(Norm.toSimplified(g), 1, Int::plus)
            if ("全彩" in ts) color++
            if ("长篇" in ts) long++
        }
        // 维度
        val dims = LinkedHashMap<String, HashMap<String, Int>>()
        for ((t, c) in tagCount) dimensionOf(t)?.let { dims.getOrPut(it) { HashMap() }[t] = c }
        val dimensions = DIMENSIONS.mapNotNull { (name, _) ->
            dims[name]?.let { d -> mapOf("name" to name, "top" to top(d, 8).map { mapOf("tag" to it.key, "count" to it.value, "share" to pct3(it.value.toDouble() / n)) }) }
        }
        val sets = tagSets.map { it.toSet() }
        val axes = AXES.mapValues { (_, keys) -> pct3(sets.count { s -> s.any { match(it, keys) } }.toDouble() / n) }
        val bgSets = TasteData.backgroundWorks(db).map { w -> Norm.normTags(w.tags).toSet() }
        val sexo = SEXO.map { (name, term, keys) ->
            val share = sets.count { s -> s.any { match(it, keys) } }.toDouble() / n
            val base = if (bgSets.size >= 30) (bgSets.count { s -> s.any { match(it, keys) } } + 0.5) / (bgSets.size + 1) else 0.0
            linkedMapOf("name" to name, "term" to term, "share" to r3(share), "base" to r3(base), "ratio" to if (base > 0) r2(share / base) else null)
        }
        // 新旧变化
        val bySite = LinkedHashMap<String, MutableList<Pair<List<String>, Int>>>()
        for ((f, ts) in favs.zip(tagSets)) bySite.getOrPut(f.w.site) { ArrayList() }.add(ts to f.rank)
        val recent = HashMap<String, Int>(); val early = HashMap<String, Int>(); var nRecent = 0; var nEarly = 0
        for ((_, items) in bySite) {
            items.sortBy { it.second }
            val q = maxOf(1, items.size / 4)
            for ((ts, _) in items.take(q)) { for (t in ts) recent.merge(t, 1, Int::plus); nRecent++ }
            for ((ts, _) in items.takeLast(q)) { for (t in ts) early.merge(t, 1, Int::plus); nEarly++ }
        }
        val drift = ArrayList<Map<String, Any?>>()
        for (t in recent.keys + early.keys) {
            val rc = recent[t] ?: 0; val ec = early[t] ?: 0
            if (rc + ec < 6) continue
            val r = rc.toDouble() / maxOf(1, nRecent); val e = ec.toDouble() / maxOf(1, nEarly)
            drift.add(mapOf("tag" to t, "recent" to r3(r), "early" to r3(e), "delta" to r3(r - e)))
        }
        val rising = drift.sortedByDescending { it["delta"].dbl() }.filter { it["delta"].dbl() > 0.03 }.take(6)
        val falling = drift.sortedBy { it["delta"].dbl() }.filter { it["delta"].dbl() < -0.03 }.take(6)
        // 口味簇（和推荐同一套 NMF）
        val favWorks = favs.map { it.w }
        val th = Themes(favWorks, TasteData.backgroundWorks(db), exclude = hidden)
        val hasCl = th.clusters.isNotEmpty()
        val clusters = th.clusters.mapIndexed { ci, c ->
            val ac = HashMap<String, Int>()
            for (i in c.members) for (a in favs[i].w.authors) { val k = Norm.normPerson(a); if (k in authorCount) ac.merge(k, 1, Int::plus) }
            val rep = c.members.sortedBy { 1.0 - th.favMix[it, ci] }.take(4)
            val inside = HashMap<String, Int>(); for (i in c.members) for (t in tagSets[i]) inside.merge(t, 1, Int::plus)
            val m = maxOf(1, c.members.size).toDouble()
            val sig = inside.filter { it.value / m >= 0.25 }.map { it.key to (it.value / m) / (tagCount.getValue(it.key).toDouble() / n) }
                .sortedByDescending { it.second }.take(3).map { it.first }
            linkedMapOf("size" to c.size, "share" to c.share, "tags" to c.tags, "name" to c.tags.take(2).joinToString("·"),
                "authors" to top(ac, 3).map { authorName.getValue(it.key) }, "works" to rep.map { brief(favs[it].w) }, "signature" to sig)
        }
        // 孤例
        val outliers = ArrayList<Map<String, Any?>>()
        if (hasCl && n >= 40) {
            val nearest = th.outlierScore(favWorks)
            val rarity = tagSets.mapIndexed { i, ts -> val rare = ts.filter { (tagCount[it] ?: 0) <= 3 && !EVENT.matches(it) }; (nearest[i] + 0.08 * rare.size) to rare }
            for (i in (0 until n).sortedByDescending { rarity[it].first }) {
                if (outliers.size >= 6) break
                if (tagSets[i].isEmpty()) continue
                outliers.add(brief(favs[i].w).apply { put("rare_tags", rarity[i].second.take(4)) })
            }
        }
        val lonely = tagCount.filter { it.value == 1 }.keys.toList()
        val honmei = db.q("SELECT site, wid FROM calibration WHERE level = 4 ORDER BY at DESC LIMIT 40")
            .mapNotNull { db.getWork(it["site"].str(), it["wid"].str())?.let { w -> brief(w) } }
        val keys = HashMap<String, MutableSet<String>>()
        for (f in favs) { val k = Norm.titleKey(f.w.title); if (k.length >= 4) keys.getOrPut(k) { HashSet() }.add(f.w.site) }
        val fb = db.q("SELECT label, COUNT(*) n FROM feedback GROUP BY label").associate { it["label"].str() to it["n"].int() }
        val topTags = top(tagCount, 30).map { mapOf("tag" to it.key, "count" to it.value, "share" to r3(it.value.toDouble() / n)) }
        val byRank = favs.associate { it.w.key to it.rank }
        val loyal = ArrayList<Map<String, Any?>>()
        for ((i, e) in top(authorCount, 10).withIndex()) {
            if (e.value < 2) continue
            val ws: List<Map<String, Any?>> = if (i < 8) authorWorks.getValue(e.key).sortedBy { byRank[it.key] ?: Int.MAX_VALUE }.take(3).map { brief(it) } else emptyList()
            loyal.add(mapOf("name" to authorName.getValue(e.key), "count" to e.value, "works" to ws))
        }
        val mosaic = ArrayList<Map<String, Any?>>(); val seen = HashSet<String>()
        val pool = honmei + clusters.flatMap { it["works"] as List<Map<String, Any?>> } + favs.sortedBy { it.rank }.take(24).map { brief(it.w) }
        for (b in pool) { if (seen.add("${b["site"]}|${b["wid"]}")) mosaic.add(b); if (mosaic.size >= 18) break }
        return linkedMapOf(
            "empty" to false, "total" to n, "per_site" to perSite,
            "distinct_authors" to authorCount.size, "distinct_parodies" to parodyCount.size,
            "repeat_authors" to authorCount.values.count { it >= 2 },
            "avg_tags" to r1(tagSets.sumOf { it.size }.toDouble() / n),
            "color_share" to r3(color.toDouble() / n), "long_share" to r3(long.toDouble() / n),
            "both_sites" to keys.values.count { it.size > 1 },
            "top_tags" to topTags, "dimensions" to dimensions, "axes" to axes, "sexology" to sexo, "authors" to loyal,
            "parodies" to top(parodyCount, 8).filter { it.value >= 2 }.map { mapOf("name" to parodyName.getValue(it.key), "count" to it.value) },
            "groups" to top(groupCount, 5).filter { it.value >= 3 }.map { mapOf("name" to it.key, "count" to it.value) },
            "rising" to rising, "falling" to falling, "clusters" to clusters, "outliers" to outliers,
            "lonely_tags" to mapOf("count" to lonely.size, "examples" to lonely.take(12)),
            "one_book_authors" to authorCount.values.count { it == 1 },
            "honmei" to honmei, "mosaic" to mosaic, "hidden_tags" to hidden.sorted(),
            "feedback" to listOf("want", "skip", "dislike", "seen", "seen_like").associateWith { fb[it] ?: 0 },
        )
    }

    /** 给 AI 看的精简版：统计 + 一部分作品标题（调音里评过的一定在内，再加各簇代表作、孤例、最新收藏）。 */
    @Suppress("UNCHECKED_CAST")
    fun digest(s: Map<String, Any?>, db: DB): Map<String, Any?> {
        fun pct(x: Any?) = "${Math.round(x.dbl() * 100)}%"
        fun fmt(b: Map<String, Any?>): String { val a = b["authors"].strList(); return b["title"].str() + if (a.isNotEmpty()) "（${a.joinToString("、")}）" else "" }
        val works = LinkedHashMap<String, Any?>()
        val lv = mapOf(4 to "本命", 3 to "喜欢", 2 to "一般", 1 to "其实不太喜欢")
        val rated = LinkedHashMap<String, MutableList<String>>()
        for (r in db.q("SELECT site, wid, level, reasons FROM calibration WHERE level > 0 ORDER BY level DESC, at DESC")) {
            val w = db.getWork(r["site"].str(), r["wid"].str()) ?: continue
            val why = Json.parse(r["reasons"].str().ifEmpty { "[]" }).strList().joinToString("、")
            rated.getOrPut(lv[r["level"].int()] ?: "?") { ArrayList() }.add(fmt(brief(w)) + if (why.isNotEmpty()) " ——因为$why" else "")
        }
        if (rated.isNotEmpty()) works["你在调音里亲自评过的"] = rated.mapValues { it.value.take(15) }
        val newest = favorites(db).filter { it.rank < 6 }.map { fmt(brief(it.w)) }
        if (newest.isNotEmpty()) works["最新收藏的几本"] = newest
        val clusters = s["clusters"].arr().map { it.obj() }
        works["各口味簇的代表作"] = clusters.associate { c -> c["name"].str() to c["works"].arr().take(3).map { fmt(it.obj()) } }
        val outl = s["outliers"].arr().map { it.obj() }
        if (outl.isNotEmpty()) works["孤例（和主流口味都不像的几本）"] = outl.map { b ->
            val rt = b["rare_tags"].strList(); fmt(b) + if (rt.isNotEmpty()) "，带着 ${rt.joinToString("、")}" else "" }
        val dims = s["dimensions"].arr().map { it.obj() }
        return linkedMapOf(
            "作品举例" to works, "收藏总数" to s["total"], "各站" to s["per_site"],
            "作者数" to s["distinct_authors"], "收藏过2本以上的作者数" to s["repeat_authors"],
            "全彩占比" to pct(s["color_share"]), "长篇占比" to pct(s["long_share"]),
            "最常见标签(占收藏比例)" to s["top_tags"].arr().take(25).associate { val t = it.obj(); t["tag"].str() to pct(t["share"]) },
            "各维度最常见标签" to dims.associate { d -> d["name"].str() to d["top"].arr().take(6).map { val t = it.obj(); "${t["tag"].str()} ${pct(t["share"])}" } },
            "倾向指数(命中比例)" to s["axes"].obj().mapValues { pct(it.value) },
            "本命作者(收藏本数)" to s["authors"].arr().take(8).associate { val a = it.obj(); a["name"].str() to a["count"] },
            "常见原作" to s["parodies"].arr().take(6).associate { val p = it.obj(); p["name"].str() to p["count"] },
            "口味簇" to clusters.map { c -> mapOf("占比" to pct(c["share"]), "代表标签" to c["tags"], "代表作者" to c["authors"]) },
            "近期上升的标签" to s["rising"].arr().map { val d = it.obj(); "${d["tag"].str()}（早期${pct(d["early"])}→近期${pct(d["recent"])}）" },
            "近期下降的标签" to s["falling"].arr().map { val d = it.obj(); "${d["tag"].str()}（早期${pct(d["early"])}→近期${pct(d["recent"])}）" },
            "在推荐里的表态" to s["feedback"],
            "只收过一本的作者数" to s["one_book_authors"],
            "只出现过一次的标签数" to s["lonely_tags"].obj()["count"],
        )
    }

    /** 在收藏里按标题 / 作者找，给“手动添加本命”用。 */
    fun searchFavorites(db: DB, q0: String, limit: Int = 20): List<Map<String, Any?>> {
        val q = Norm.norm(q0)
        if (q.isEmpty()) return emptyList()
        val honmei = db.q("SELECT site, wid FROM calibration WHERE level = 4").map { it["site"].str() to it["wid"].str() }.toSet()
        val out = ArrayList<Map<String, Any?>>()
        for (site in listOf("jm", "pica")) for (w in db.collection(site)) {
            val hay = Norm.norm(w.title) + " " + w.authors.joinToString(" ") { Norm.normPerson(it) }
            if (q in hay) { out.add(brief(w).apply { put("honmei", w.key in honmei) }); if (out.size >= limit) return out }
        }
        return out
    }
}

/** AI 侧写：生成一段提示词，复制或分享到你常用的 AI 应用（对应 portrait.py，改成直接读的文字，限字数）。 */
object Portrait {
    val PROMPT = """你是一位读过很多成人漫画、懂一点精神分析、也很会聊天的“口味侧写师”。
下面是一位用户在两个成人漫画站（哔咔、禁漫）的收藏摘要：标签占比、倾向指数、本命作者、常见原作、口味簇、
早期到近期的口味变化，还有一些具体作品的标题（其中“你在调音里亲自评过的”是用户亲手标的本命 / 喜欢 / 不喜欢，分量最重）。
用户本人主动请你为 TA 做一份口味画像，TA 是成年人，知道这些内容的性质。

想怎么读都可以，自由发挥：可以从欲望的结构、幻想替你完成了什么、亲密与权力的位置、口味的迁徙讲起，
也可以从某几本具体作品、某个孤例、某个作者切进去；可以认真，也可以放肆地猜 TA 的生活、性格、恋爱里的样子。
认真的部分要有依据（指回数字或作品），用“也许”“可能”；猜的部分要好玩、带点损但不刻薄。

只守几条底线：不做道德评判和说教；不做精神疾病诊断，不把偏好病理化；只在主题和偏好层面讨论，不写露骨的性描写；
涉及年龄看起来偏小的标签（如“萝莉”“学生”“JK”）时，只当作画风或角色设定中性带过，不做性化展开；
不编造摘要里没有的作品或作者。用第二人称“你”，简体中文，有文采、有梗。

直接用文字回答（不要代码块、不要 JSON），按下面的结构，全文控制在 900 字以内：
1. 第一行：一个有画面感的人格称号（4 到 12 个字）
2. 一句让人想截图的总结（40 字以内，不评判）
3. 三到六个关键词
4. 三到四段认真的解读，每段配一个小标题
5. 口味的迁徙：一小段（没什么可说就略过）
6. 四到六条大胆的猜测，每条标上“合理推测 / 大胆推测 / 纯属瞎猜”
7. 最后一句：下一卷可以多给什么、可以试探什么

用户的口味摘要：
"""

    fun prompt(db: DB): String {
        val s = Insights.summary(db)
        if (s["empty"] == true) return ""
        return PROMPT + Json.stringify(Insights.digest(s, db), 1)
    }
}
