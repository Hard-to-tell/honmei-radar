package honmei.algo

import honmei.*
import honmei.sources.SiteAdapter
import kotlin.math.roundToInt
import kotlin.math.pow

/** 生成一卷禁漫推荐（对应 recommend.py）。 */
object Recommend {
    const val SEARCH_TTL = 24 * 3600.0
    val ROUTE_NAMES = mapOf("author" to "熟悉的笔", "tag" to "同频", "parody" to "同一世界", "related" to "相邻", "explore" to "偶遇", "cold" to "初见")

    fun cachedSearch(db: DB, ad: SiteAdapter, key: String, fn: () -> List<Work>): List<Work> {
        val hit = db.kvGet("search:$key").obj()
        if (hit.isNotEmpty() && now() - hit["at"].dbl() < SEARCH_TTL) {
            val ids = hit["ids"].strList()
            val works = db.getWorks(ids.map { ad.site to it })
            return ids.mapNotNull { works[ad.site to it] }
        }
        val res = try { fn() } catch (e: SiteError) { Env.logLine("检索失败 $key：${e.message}"); return emptyList() }
        for (w in res) db.upsertWork(w)
        db.kvSet("search:$key", mapOf("at" to now(), "ids" to res.map { it.wid }))
        return res
    }

    fun buildProfile(db: DB): Profile {
        val fb = db.feedbackMap()
        val favs = linkedMapOf("jm" to db.collection("jm"), "pica" to db.collection("pica"))
        val negs = fb.filter { it.value.label in setOf("skip", "dislike") }.keys.mapNotNull { db.getWork(it.first, it.second) }
        return Profile.build(favs, negs)
    }

    fun buildTaste(db: DB, profile: Profile, clusters: Boolean = true): TasteModel {
        val fb = db.feedbackMap()
        val feedback = ArrayList<Pair<Work, Any>>()
        for ((k, f) in fb) if (f.label in setOf("want", "seen_like", "skip", "dislike")) db.getWork(k.first, k.second)?.let { feedback.add(it to f.label) }
        val cal = db.calibrationMap()
        val favKeys = profile.favorites.map { it.key }.toSet()
        for ((k, c) in cal) if (k !in favKeys && c.level > 0) db.getWork(k.first, k.second)?.takeIf { it.detailOk }?.let { feedback.add(it to c.level) }
        val tm = TasteModel(profile.favorites, profile.favWeights, TasteData.backgroundWorks(db), feedback, clusters, cal)
        if (clusters) db.kvSet("taste:clusters", tm.clusters.size)
        return tm
    }

    class Summary(val authors: List<String>, val tags: List<String>, val parodies: List<String>)

    fun tasteSummary(p: Profile, top: Int = 8): Summary {
        val authors = LinkedHashMap<String, Double>(); val names = HashMap<String, String>()
        val tags = LinkedHashMap<String, Double>(); val parodies = LinkedHashMap<String, Double>()
        for ((i, w) in p.favorites.withIndex()) {
            val wt = p.favWeights[i]
            for (pp in Feat.workPeople(w)) if (pp.weight == 1.0) { authors[pp.key] = (authors[pp.key] ?: 0.0) + wt; names.putIfAbsent(pp.key, pp.raw) }
            for (t in Feat.workTokens(w)) if (t.startsWith("原作:")) parodies[t.substring(3)] = (parodies[t.substring(3)] ?: 0.0) + wt else tags[t] = (tags[t] ?: 0.0) + wt
            for (pr in w.parodies) names.putIfAbsent("原作:" + Norm.norm(pr), pr)
        }
        val n = maxOf(1, p.favorites.size)
        fun <K> mostCommon(m: Map<K, Double>, k: Int) = m.entries.sortedByDescending { it.value }.take(k)
        return Summary(
            mostCommon(authors, top).map { names.getValue(it.key) },
            mostCommon(tags, top * 3).filter { it.value / n < 0.6 }.take(top).map { it.key },
            mostCommon(parodies, 4).map { names["原作:" + it.key] ?: it.key },
        )
    }

    fun gatherCandidates(db: DB, jm: SiteAdapter, p: Profile, progress: Progress?, taste: TasteModel?): Pair<LinkedHashMap<String, Pair<Work, String>>, HashMap<String, MutableList<String>>> {
        val found = LinkedHashMap<String, Pair<Work, String>>(); val hints = HashMap<String, MutableList<String>>()
        fun add(works: Collection<Work>, route: String, tags: List<String>? = null) {
            for (w in works) {
                if (w.wid !in found) found[w.wid] = w to route
                if (tags != null) { val h = hints.getOrPut(w.wid) { ArrayList() }; for (t in tags.mapNotNull { Norm.normTag(it) }) if (t !in h) h.add(t) }
            }
        }
        val ts = tasteSummary(p)
        val steps = ArrayList<Pair<String, String>>()
        ts.authors.forEach { steps.add("author" to it) }
        if (taste != null && taste.clusters.isNotEmpty()) {
            for ((_, tags) in taste.clusterQueries()) { steps.add("tag" to tags[0]); if (tags.size > 1) steps.add("tag2" to "${tags[0]} ${tags[1]}") }
        } else {
            ts.tags.take(5).forEach { steps.add("tag" to it) }
            for ((a, b) in ts.tags.take(3).zip(ts.tags.drop(1).take(3))) steps.add("tag2" to "$a $b")
        }
        ts.parodies.forEach { steps.add("parody" to it) }
        val uniq = steps.distinct()
        for ((i, st) in uniq.withIndex()) {
            val (kind, q) = st
            progress.say("循着“$q”寻找（${i + 1}/${uniq.size}）", i + 1, uniq.size)
            when (kind) {
                "author" -> add(cachedSearch(db, jm, "author:$q") { jm.search(q, "author", 1, "latest") }, "author")
                "tag" -> add(cachedSearch(db, jm, "tag:$q") { jm.search(q, "tag", 1, "like") }, "tag", listOf(q))
                "tag2" -> add(cachedSearch(db, jm, "site:$q") { jm.search(q, "site", 1, "like") }, "tag", q.split(" "))
                "parody" -> add(cachedSearch(db, jm, "work:$q") { jm.search(q, "work", 1, "like") }, "parody")
            }
        }
        for (w in db.collection("jm").take(15)) {
            val ids = db.kvGet("related:jm:${w.wid}").strList()
            add(db.getWorks(ids.map { "jm" to it }).values, "related")
        }
        progress.say("顺路看看站内正热的作品")
        add(cachedSearch(db, jm, "rank:week:1") { jm.ranking("week", 1) }, "explore")
        add(cachedSearch(db, jm, "rank:month:1") { jm.ranking("month", 1) }, "explore")
        return found to hints
    }

    fun blockedTagSet(settings: Map<String, Any?>): Set<String> = settings["blocked_tags"].strList().mapNotNull { Norm.normTag(it) }.toSet()

    class Excl(val jmIds: Set<String>, val keys: Set<String>, val blocked: Set<Pair<String, String>>)

    fun exclusion(db: DB): Excl {
        val jmIds = db.collection("jm").map { it.wid }.toSet()
        val keys = HashSet<String>()
        for (w in db.collection()) { keys.addAll(Norm.dedupKeys(w.title, w.authors)); val k = Norm.titleKey(w.title); if (k.length >= 4) keys.add("t:$k") }
        val fb = db.feedbackMap()
        val blocked = HashSet(fb.keys)
        for ((s, wid) in fb.keys) db.getWork(s, wid)?.let { keys.addAll(Norm.dedupKeys(it.title, it.authors)) }
        for (r in db.q("SELECT DISTINCT site, wid FROM batch_items WHERE batch_id > ?", db.batchOffset())) blocked.add(r["site"].str() to r["wid"].str())
        return Excl(jmIds, keys, blocked)
    }

    const val DIV_DEFAULT = 0.2
    val MODES: Map<String, Map<String, Any>> = linkedMapOf(
        "balance" to mapOf("name" to "平衡"),
        "safe" to mapOf("name" to "稳妥", "author" to 0.15, "fit" to 0.10, "explore" to 0.5, "div" to 0.0, "per_author" to 3),
        "fresh" to mapOf("name" to "尝鲜", "author" to -0.22, "explore" to 2.0, "div" to 0.5, "per_author" to 1, "new_author" to 0.08),
        "niche" to mapOf("name" to "冷门", "pop" to -0.30, "explore" to 1.0, "hot" to false),
    )

    /** 候选两两之间有多像（标签 + 原作，0~1）。格式标签不算。 */
    fun similarity(cands: List<Work>): Mat {
        val docs = cands.map { w -> Feat.workTokens(w).filter { it !in Themes.FORMAT_TAGS }.map { it.replace(" ", "_").lowercase() } }
        if (docs.all { it.isEmpty() }) return Mat(cands.size, cands.size)
        val x = Tfidf.fit(docs, sublinear = true)
        val s = Tfidf.sims(x, x)
        for (i in 0 until s.rows) s[i, i] = 0.0
        return s
    }

    private fun quotas(mainK: Int, cinfo: List<Themes.Cluster>, rnd: java.util.Random, power: Double = 0.5): MutableMap<Int, Int> {
        val live = cinfo.withIndex().filter { !it.value.muted }
        val raw = live.associate { it.index to it.value.share.pow(power) * it.value.weight }
        val tot = raw.values.sum().takeIf { it > 0 } ?: 1.0
        val q = raw.mapValues { maxOf(1, Math.rint(mainK * it.value / tot).toInt()) }.toMutableMap()
        for ((ci, c) in cinfo.withIndex()) if (c.muted) q[ci] = if (rnd.nextDouble() < 0.4) 1 else 0
        return q
    }

    fun pick(cands: List<Work>, scores: List<Double>, routes: List<String>, k: Int, exploreK: Int, maxPerAuthor: Int, rnd: java.util.Random,
             clusters0: List<Int>?, cinfo: List<Themes.Cluster>, power: Double = 0.5, hotK: Int = 1, div: Double? = null): List<Int> {
        val order = cands.indices.sortedByDescending { scores[it] }
        val chosen = ArrayList<Int>(); val chosenSet = HashSet<Int>()
        val perAuthor = HashMap<String, Int>(); val perParody = HashMap<String, Int>(); val perCluster = HashMap<Int, Int>()
        val seenKeys = HashSet<String>()
        val ckeys = cands.map { Norm.dedupKeys(it.title, it.authors) }
        val clusters = clusters0 ?: List(cands.size) { -1 }
        val mainK = k - exploreK
        val quota = if (cinfo.isNotEmpty()) quotas(mainK, cinfo, rnd, power) else mutableMapOf()
        val muted = cinfo.indices.filter { cinfo[it].muted }.toSet()
        fun ok(i: Int): Boolean {
            if (ckeys[i].any { it in seenKeys }) return false
            val people = Feat.workPeople(cands[i]).take(1).map { it.key }
            if (people.any { (perAuthor[it] ?: 0) >= maxPerAuthor }) return false
            val par = cands[i].parodies.take(1).map { Norm.norm(it) }
            if (par.any { (perParody[it] ?: 0) >= maxOf(3, k / 4) }) return false
            val c = clusters[i]
            if (c in muted && (perCluster[c] ?: 0) >= (quota[c] ?: 0)) return false
            return true
        }
        fun take(i: Int) {
            chosen.add(i); chosenSet.add(i); seenKeys.addAll(ckeys[i])
            Feat.workPeople(cands[i]).take(1).forEach { perAuthor.merge(it.key, 1, Int::plus) }
            cands[i].parodies.take(1).forEach { perParody.merge(Norm.norm(it), 1, Int::plus) }
            perCluster.merge(clusters[i], 1, Int::plus)
        }
        if (div != null) {
            val s = scores; val mn = s.minOrNull() ?: 0.0; val span = (s.maxOrNull() ?: 0.0) - mn
            val rel = s.map { if (span > 0) (it - mn) / span else 0.0 }
            val sim = similarity(cands)
            val maxsim = DoubleArray(cands.size)
            for (useQuota in if (quota.isNotEmpty()) listOf(true, false) else listOf(false)) {
                while (chosen.size < mainK) {
                    var best = -1; var bestV = -1e9
                    for (i in order) {
                        if (i in chosenSet || !ok(i)) continue
                        val c = clusters[i]
                        if (useQuota && c in quota && (perCluster[c] ?: 0) >= quota.getValue(c)) continue
                        val v = (1 - div) * rel[i] - div * maxsim[i]
                        if (v > bestV) { best = i; bestV = v }
                    }
                    if (best < 0) break
                    take(best)
                    for (j in maxsim.indices) maxsim[j] = maxOf(maxsim[j], sim[best, j])
                }
            }
        } else {
            for (i in order) {
                if (chosen.size >= mainK) break
                val c = clusters[i]
                if (c in quota && (perCluster[c] ?: 0) >= quota.getValue(c)) continue
                if (ok(i)) take(i)
            }
            for (i in order) { if (chosen.size >= mainK) break; if (i !in chosenSet && ok(i)) take(i) }
        }
        val hot = order.filter { routes[it] == "explore" && it !in chosenSet }.shuffled(rnd)
        for (i in hot.take(hotK)) if (chosen.size < k && ok(i)) take(i)
        val live = cinfo.indices.filter { it !in muted }.toMutableList()
        var tries = 0
        while (chosen.size < k && live.isNotEmpty() && tries < 4 * k) {
            tries++
            val ws = live.map { cinfo[it].uncertainty + 0.05 }
            var r = rnd.nextDouble() * ws.sum(); var ci = live.last()
            for ((j, c) in live.withIndex()) { r -= ws[j]; if (r < 0) { ci = c; break } }
            val pool = order.filter { clusters[it] == ci && it !in chosenSet && ok(it) }.take(3)
            if (pool.isEmpty()) { live.remove(ci); continue }
            take(pool[0])
        }
        for (i in order) { if (chosen.size >= k) break; if (i !in chosenSet && ok(i)) take(i) }
        for (i in order) { if (chosen.size >= k) break; if (i !in chosenSet && ckeys[i].none { it in seenKeys }) take(i) }
        return chosen
    }

    fun reasonText(reason: Map<String, Any?>, route: String): String {
        if (route == "explore" && reason["author"].str().isEmpty() && reason["nearest"].arr().isEmpty()) return "站内正热，和你平日的口味不太一样，换换口味"
        if (route == "cold") return "题材各异的热门作品，挑几本告诉我心不心动"
        val parts = ArrayList<String>()
        if (reason["author"].str().isNotEmpty()) parts.add("出自你熟悉的${reason["author_kind"].str().ifEmpty { "作者" }}「${reason["author"].str()}」")
        val nearest = reason["nearest"].arr()
        if (nearest.isNotEmpty()) parts.add("与你收藏的《${nearest.take(2).joinToString("》《") { it.obj()["title"].str().take(24) }}》气味相近")
        val shared = reason["shared_tags"].strList()
        if (shared.isNotEmpty()) parts.add("同样带着 " + shared.take(3).joinToString("、"))
        if (parts.isEmpty()) parts.add("循着“${ROUTE_NAMES[route] ?: route}”找到")
        return parts.joinToString("；")
    }

    private fun head(pool: List<Pair<Work, String>>, order: List<Int>, n: Int, perAuthor: Int): MutableList<Pair<Work, String>> {
        val head = ArrayList<Pair<Work, String>>(); val used = HashSet<Int>(); val byAuthor = HashMap<String, Int>()
        val authorCap = n / 2
        for (i in order) {
            if (head.size >= n) break
            val (w, route) = pool[i]
            val who = Feat.workPeople(w).firstOrNull()?.key ?: ""
            if (who.isNotEmpty() && (byAuthor[who] ?: 0) >= perAuthor) continue
            if (route == "author" && head.count { it.second == "author" } >= authorCap) continue
            head.add(pool[i]); used.add(i)
            if (who.isNotEmpty()) byAuthor.merge(who, 1, Int::plus)
        }
        for (i in order) { if (head.size >= n) break; if (i !in used) { head.add(pool[i]); used.add(i) } }
        return head
    }

    fun generateBatch(db: DB, jm: SiteAdapter, progress: Progress?, seed: Long? = null, mode0: String? = null): Long {
        val settings = Settings.load()
        val k = settings["batch_size"].int().takeIf { it > 0 } ?: 10
        val mode = if (mode0 in MODES) mode0!! else settings["rec_mode"].str().takeIf { it in MODES } ?: "balance"
        val mc = MODES.getValue(mode)
        fun mcd(key: String) = (mc[key] as? Number)?.toDouble() ?: 0.0
        val rnd = java.util.Random(seed ?: System.nanoTime())
        val semantic = NearTags.enabled

        progress.say("翻阅你的收藏，整理口味")
        try { TasteData.learnAliases(db) } catch (e: Exception) { Env.logLine("跨站标签对照没学成：${e.message}") }
        val profile = buildProfile(db)
        if (!profile.empty && TasteData.backgroundStale(db)) {
            try { TasteData.refreshBackground(db, jm, progress) } catch (e: SiteError) { Env.logLine("基准样本没取全：${e.message}") }
        }
        val taste = if (profile.empty) null else buildTaste(db, profile)
        var hints: Map<String, List<String>> = emptyMap()
        val found: LinkedHashMap<String, Pair<Work, String>>
        if (profile.empty) {
            found = LinkedHashMap()
            for (kind in listOf("week", "month")) for (w in cachedSearch(db, jm, "rank:$kind:1") { jm.ranking(kind, 1) }) found.putIfAbsent(w.wid, w to "cold")
        } else {
            val r = gatherCandidates(db, jm, profile, progress, taste); found = r.first; hints = r.second
        }
        val ex = exclusion(db)
        val banned = blockedTagSet(settings)
        val pool = ArrayList<Pair<Work, String>>()
        for ((wid, wr) in found) {
            val (w, _) = wr
            if (wid in ex.jmIds || ("jm" to wid) in ex.blocked) continue
            if (banned.isNotEmpty() && Norm.normTags(w.tags).any { it in banned }) continue
            if (Norm.dedupKeys(w.title, w.authors).any { it in ex.keys }) continue
            pool.add(wr)
        }
        if (pool.isEmpty()) throw SiteError("没有找到新的候选，可能是网络不通或收藏还没导入")

        progress.say("从 ${pool.size} 本候选里初筛")
        val bg = pool.map { it.first }
        val pre0 = FeatureBuilder(profile, bg, false, taste, hints).build(bg).rows
        val aW = maxOf(0.0, 0.3 + mcd("author"))
        val pre = pre0.map { 0.25 * it.getValue("tag") + 0.3 * it.getValue("fit") + aW * it.getValue("author") + 0.15 * it.getValue("title") }
        val order = pool.indices.sortedByDescending { pre[it] }
        val perAuthorHead = maxOf(2, ((mc["per_author"] as? Number)?.toInt() ?: 2) + 1)
        val hd = head(pool, order, minOf(pool.size, k * 5), perAuthorHead)
        val headSet = hd.map { it.first.wid }.toSet()
        hd.addAll(pool.filter { (it.second == "explore" || it.second == "cold") && it.first.wid !in headSet }.take(maxOf(4, k / 4)))

        Importer.ensureDetails(db, jm, hd.map { it.first.wid }, progress, "细读候选：")
        val cands = ArrayList<Work>(); val routes = ArrayList<String>()
        for ((w, route) in hd) {
            val full = db.getWork("jm", w.wid) ?: w
            if (banned.isNotEmpty() && Norm.normTags(full.tags).any { it in banned }) continue
            cands.add(full); routes.add(route)
        }
        if (cands.isEmpty()) throw SiteError("候选都被拉黑的标签挡掉了，换个口味模式或少拉黑几个标签再试")

        progress.say("掂量每一本与你的距离")
        val built = FeatureBuilder(profile, cands + pool.map { it.first }, semantic, taste).build(cands)
        val feats = built.rows; val reasons = built.reasons
        val state = Ranker.train(db, semantic)
        val (scores, engine) = Ranker.finalScores(feats, semantic, state)
        val cl = taste?.clusterOf(cands)
        if (cl != null && taste != null) for ((i, ci) in cl.withIndex()) if (ci >= 0) {
            val c = taste.clusters[ci]
            scores[i] += 0.1 * c.practical * (feats[i].getValue("fit") - 0.5) + 0.1 * c.story * (feats[i]["semantic"] ?: 0.0)
        }
        if (mode != "balance") for ((i, f) in feats.withIndex()) {
            scores[i] += mcd("author") * f.getValue("author") + mcd("fit") * (f.getValue("fit") - 0.5) + mcd("pop") * f.getValue("pop") +
                mcd("new_author") * (if (f.getValue("author") < 0.05) 1.0 else 0.0)
        }
        val exploreK = if (profile.empty) 0 else maxOf(1, Math.rint(k * settings["explore_ratio"].dbl() * ((mc["explore"] as? Number)?.toDouble() ?: 1.0)).toInt())
        val cinfo = taste?.clusters ?: emptyList()
        val perAuthor = (mc["per_author"] as? Number)?.toInt() ?: settings["max_per_author"].int().takeIf { it > 0 } ?: 2
        val div = if ("div" in mc) mcd("div") else DIV_DEFAULT
        val chosen = pick(cands, scores, routes, k, exploreK, perAuthor, rnd, cl, cinfo, hotK = if (mc["hot"] == false) 0 else 1, div = div).toMutableList()
        chosen.shuffle(rnd)
        val items = chosen.map { i ->
            val r = LinkedHashMap(reasons[i])
            r["text"] = reasonText(r, routes[i])
            r["route_name"] = ROUTE_NAMES[routes[i]] ?: routes[i]
            if (cl != null && cl[i] >= 0 && taste != null) { r["cluster"] = cl[i]; r["cluster_tags"] = taste.clusters[cl[i]].tags.take(3) }
            mapOf("site" to "jm", "wid" to cands[i].wid, "score" to r4(scores[i]), "features" to feats[i], "reason" to r, "route" to routes[i])
        }
        val meta = linkedMapOf("pool" to pool.size, "ranker" to state["mode"], "ranker_message" to (state["message"] ?: ""),
            "semantic_error" to "", "algo" to "v2", "mode" to mode, "mode_name" to mc["name"], "near_tags" to semantic,
            "background" to (taste?.background?.size ?: 0), "favorites" to mapOf("jm" to db.collection("jm").size, "pica" to db.collection("pica").size))
        val bid = db.createBatch(engine, items, meta)
        progress.say("Vol.${bid - db.batchOffset()} 已成卷，共 ${items.size} 本")
        return bid
    }

    fun batchView(db: DB, bid: Long): Map<String, Any?>? {
        val meta = db.batchMeta(bid) ?: return null
        val fb = db.feedbackMap()
        val banned = blockedTagSet(Settings.load())
        val items = ArrayList<Map<String, Any?>>()
        for (it in db.batchItems(bid)) {
            val w = db.getWork(it["site"].str(), it["wid"].str()) ?: continue
            val f = fb[w.site to w.wid]
            val pt = Norm.parseTitle(w.title)
            items.add(linkedMapOf(
                "pos" to it["pos"], "site" to w.site, "wid" to w.wid, "title" to w.title, "core_title" to pt.core.ifEmpty { w.title },
                "authors" to w.authors, "tags" to w.tags.take(12), "parodies" to w.parodies,
                "cover" to "/api/cover/${w.site}/${w.wid}", "url" to w.url, "source" to if (w.site == "jm") "禁漫" else "哔咔",
                "reason" to it["reason"], "route" to it["route"], "score" to it["score"], "features" to it["features"],
                "feedback" to f?.label, "blocked" to w.tags.filter { t -> Norm.normTag(t) in banned },
            ))
        }
        meta["items"] = items
        return meta
    }
}
