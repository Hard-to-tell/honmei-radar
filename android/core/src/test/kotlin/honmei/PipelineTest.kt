package honmei

import honmei.algo.*
import honmei.sources.SiteAdapter
import java.io.File
import kotlin.test.Test
import kotlin.test.assertTrue

/** 假的禁漫：检索 / 排行都从数据库里随机取没收藏的作品，详情直接读库。不联网。 */
class FakeJm(val db: DB) : SiteAdapter {
    override val site = "jm"; override val name = "禁漫"
    private val pool by lazy {
        val fav = db.collection("jm").map { it.wid }.toSet()
        db.q("SELECT * FROM works WHERE site='jm' AND detail_ok=1").map { db.rowToWork(it) }.filter { it.wid !in fav }
    }
    private val rnd = java.util.Random(7)
    override fun login(username: String, password: String) = "ok"
    override fun loggedIn() = true
    override fun fetchFavorites(progress: Progress?, maxPages: Int?) = db.collection("jm")
    override fun fetchDetail(wid: String) = (db.getWork("jm", wid) ?: throw SiteError("no")) to emptyList<Work>()
    override fun search(query: String, mode: String, page: Int, order: String) = pool.shuffled(rnd).take(40).map { it.copy(detailOk = false) }
    override fun ranking(kind: String, page: Int) = pool.shuffled(rnd).take(30).map { it.copy(detailOk = false) }
    override fun browse(page: Int, category: String, order: String) = pool.shuffled(rnd).take(30)
    override fun webUrl(wid: String) = "https://example.invalid/$wid"
}

class PipelineTest {
    private val src = System.getenv("HONMEI_TEST_DB")

    @Test fun normalize() {
        assertTrue(Norm.normTag("強暴") != null)
        println("t2s: " + OpenCC.convert("漢化 戀愛 強姦"))
    }

    @Test fun pipeline() {
        if (src == null || !File(src).exists()) { println("跳过：没设 HONMEI_TEST_DB"); return }
        val dir = File(System.getProperty("java.io.tmpdir"), "honmei-test-" + System.nanoTime()).apply { mkdirs() }
        val dbf = File(dir, "shelf.db"); File(src).copyTo(dbf)
        val db = DB(JdbcSql(dbf.path))
        Env.init(dir, db)
        val svc = Service(db)
        println("近义标签表：${NearTags.table.size} 个标签")
        var t = System.currentTimeMillis()
        val prof = Recommend.buildProfile(db)
        val taste = Recommend.buildTaste(db, prof)
        println("收藏 ${prof.favorites.size} 本，口味主题 ${taste.clusters.size} 个，用时 ${System.currentTimeMillis() - t}ms")
        for (c in taste.clusters) println("  ${c.name} share=${"%.2f".format(c.share)} tags=${c.tags.take(6)}")
        t = System.currentTimeMillis()
        val cal = Calibrate.pickRound(db, 1)
        println("调音：${cal["items"].arr().size} 本，用时 ${System.currentTimeMillis() - t}ms")
        val fake = FakeJm(db)
        for (mode in listOf("balance", "fresh")) {
            t = System.currentTimeMillis()
            val bid = Recommend.generateBatch(db, fake, { m, _, _ -> if (!m.startsWith("循着")) println("   · $m") }, seed = 3, mode0 = mode)
            val v = Recommend.batchView(db, bid)!!
            val items = v["items"].arr()
            println("开卷[$mode]：${items.size} 本，用时 ${System.currentTimeMillis() - t}ms，meta=${v["meta"].obj().filterKeys { it in setOf("pool", "ranker", "mode") }}")
            for (it in items.take(3)) { val o = it.obj(); println("    ${o["route"]} score=${o["score"]} reason=${o["reason"].obj()["text"].str().take(60)}") }
            assertTrue(items.size >= 8)
            println(Explain.local(Explain.evidence(db, bid, 1)!!).lines().take(3).joinToString(" | ").take(200))
        }
        val st = Json.stringify(svc.status())
        assertTrue(st.contains("calibrate"))
    }
}
