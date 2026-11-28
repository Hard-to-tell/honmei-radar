package honmei

typealias Row = Map<String, Any?>

/** 底层 SQLite：安卓用系统自带的 SQLiteDatabase，电脑上测试用 sqlite-jdbc。 */
interface Sql {
    fun query(sql: String, args: List<Any?> = emptyList()): List<Row>
    fun exec(sql: String, args: List<Any?> = emptyList())
    fun insert(sql: String, args: List<Any?> = emptyList()): Long
    fun <T> tx(block: () -> T): T
}

/** 本地存储（对应 db.py，表结构完全一样）。 */
class DB(val sql: Sql) {
    private val lock = Any()

    init {
        for (stmt in SCHEMA.split(";").map { it.trim() }.filter { it.isNotEmpty() }) sql.exec(stmt)
    }

    fun q(s: String, vararg args: Any?): List<Row> = synchronized(lock) { sql.query(s, args.toList()) }
    fun x(s: String, vararg args: Any?) = synchronized(lock) { sql.exec(s, args.toList()) }

    fun kvGet(k: String): Any? {
        val r = q("SELECT v FROM kv WHERE k=?", k)
        return if (r.isEmpty()) null else Json.parse(r[0]["v"].str())
    }

    fun kvSet(k: String, v: Any?) = x("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", k, Json.stringify(v))

    // ---------- 作品 ----------
    fun upsertWork(w: Work) = synchronized(lock) {
        sql.tx {
            val old = sql.query("SELECT detail_ok FROM works WHERE site=? AND wid=?", listOf(w.site, w.wid)).firstOrNull()
            if (old != null && old["detail_ok"].int() != 0 && !w.detailOk) {
                sql.exec(
                    "UPDATE works SET cover=COALESCE(NULLIF(?, ''), cover), url=COALESCE(NULLIF(?, ''), url) WHERE site=? AND wid=?",
                    listOf(w.cover, w.url, w.site, w.wid),
                )
            } else {
                sql.exec(
                    "INSERT OR REPLACE INTO works(site, wid, title, authors, tags, parodies, circles, groups_, " +
                        "category, description, cover, url, popularity, detail_ok, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    listOf(
                        w.site, w.wid, w.title, Json.stringify(w.authors), Json.stringify(w.tags), Json.stringify(w.parodies),
                        Json.stringify(w.circles), Json.stringify(w.groups), w.category, w.description, w.cover, w.url,
                        w.popularity, if (w.detailOk) 1 else 0, now(),
                    ),
                )
            }
        }
    }

    fun rowToWork(r: Row): Work = Work(
        site = r["site"].str(), wid = r["wid"].str(), title = r["title"].str(),
        authors = Json.parse(r["authors"].str()).strList().toMutableList(),
        tags = Json.parse(r["tags"].str()).strList().toMutableList(),
        parodies = Json.parse(r["parodies"].str()).strList().toMutableList(),
        circles = Json.parse(r["circles"].str()).strList().toMutableList(),
        groups = Json.parse(r["groups_"].str()).strList().toMutableList(),
        category = r["category"].str(), description = r["description"].str(), cover = r["cover"].str(),
        url = r["url"].str(), popularity = r["popularity"].dbl(), detailOk = r["detail_ok"].int() != 0,
    )

    fun getWork(site: String, wid: String): Work? =
        q("SELECT * FROM works WHERE site=? AND wid=?", site, wid).firstOrNull()?.let { rowToWork(it) }

    fun getWorks(keys: List<Pair<String, String>>): LinkedHashMap<Pair<String, String>, Work> {
        val out = LinkedHashMap<Pair<String, String>, Work>()
        for ((s, w) in keys) getWork(s, w)?.let { out[s to w] = it }
        return out
    }

    // ---------- 收藏 ----------
    fun replaceCollection(site: String, ordered: List<String>) = synchronized(lock) {
        val t = now()
        sql.tx {
            sql.exec("DELETE FROM collections WHERE site=?", listOf(site))
            for ((i, w) in ordered.withIndex()) sql.exec(
                "INSERT OR IGNORE INTO collections(site, wid, rank, imported_at) VALUES (?,?,?,?)", listOf(site, w, i, t),
            )
        }
    }

    fun collection(site: String? = null): List<Work> {
        val s = "SELECT w.*, c.rank FROM collections c JOIN works w ON w.site=c.site AND w.wid=c.wid"
        val rows = if (site != null) q("$s WHERE c.site=? ORDER BY c.site, c.rank", site) else q("$s ORDER BY c.site, c.rank")
        return rows.map { rowToWork(it) }
    }

    fun collectionCounts(): Map<String, Int> =
        q("SELECT site, COUNT(*) n FROM collections GROUP BY site").associate { it["site"].str() to it["n"].int() }

    // ---------- 批次 ----------
    fun createBatch(engine: String, items: List<Map<String, Any?>>, meta: Map<String, Any?>): Long = synchronized(lock) {
        sql.tx {
            val bid = sql.insert("INSERT INTO batches(created_at, engine, meta) VALUES (?,?,?)", listOf(now(), engine, Json.stringify(meta)))
            for ((i, it) in items.withIndex()) sql.exec(
                "INSERT INTO batch_items(batch_id, pos, site, wid, score, features, reason, route) VALUES (?,?,?,?,?,?,?,?)",
                listOf(bid, i + 1, it["site"], it["wid"], it["score"], Json.stringify(it["features"]), Json.stringify(it["reason"]), it["route"]),
            )
            bid
        }
    }

    fun batchOffset(): Long = (kvGet("batches:reset").obj()["after"]).long()

    fun resetBatches(): Long {
        val after = q("SELECT MAX(id) m FROM batches")[0]["m"].long()
        kvSet("batches:reset", mapOf("after" to after, "at" to now()))
        return after
    }

    fun listBatches(limit: Int = 50): List<Map<String, Any?>> {
        val off = batchOffset()
        return q(
            "SELECT b.id, b.created_at, b.engine, b.meta, COUNT(i.pos) n FROM batches b LEFT JOIN batch_items i ON i.batch_id=b.id " +
                "WHERE b.id > ? GROUP BY b.id ORDER BY b.id DESC LIMIT ${limit.coerceAtLeast(1)}", off,
        ).map {
            mapOf("id" to it["id"].long(), "vol" to it["id"].long() - off, "created_at" to it["created_at"].dbl(),
                "engine" to it["engine"].str(), "meta" to Json.parse(it["meta"].str()), "count" to it["n"].int())
        }
    }

    fun batchItems(bid: Long): List<Map<String, Any?>> = q("SELECT * FROM batch_items WHERE batch_id=? ORDER BY pos", bid).map {
        mapOf("batch_id" to it["batch_id"].long(), "pos" to it["pos"].int(), "site" to it["site"].str(), "wid" to it["wid"].str(),
            "score" to it["score"].dbl(), "features" to Json.parse(it["features"].str()), "reason" to Json.parse(it["reason"].str()),
            "route" to it["route"].str())
    }

    fun batchMeta(bid: Long): MutableMap<String, Any?>? {
        val off = batchOffset()
        val r = q("SELECT * FROM batches WHERE id=? AND id > ?", bid, off).firstOrNull() ?: return null
        return linkedMapOf("id" to r["id"].long(), "vol" to r["id"].long() - off, "created_at" to r["created_at"].dbl(),
            "engine" to r["engine"].str(), "meta" to Json.parse(r["meta"].str()))
    }

    // ---------- 反馈 ----------
    fun setFeedback(site: String, wid: String, label: String?, bid: Long?) {
        if (label == null) x("DELETE FROM feedback WHERE site=? AND wid=?", site, wid)
        else x("INSERT OR REPLACE INTO feedback(site, wid, label, batch_id, created_at) VALUES (?,?,?,?,?)", site, wid, label, bid, now())
    }

    data class Fb(val label: String, val batchId: Long?, val createdAt: Double)

    fun feedbackMap(): LinkedHashMap<Pair<String, String>, Fb> {
        val m = LinkedHashMap<Pair<String, String>, Fb>()
        for (r in q("SELECT * FROM feedback")) m[r["site"].str() to r["wid"].str()] =
            Fb(r["label"].str(), r["batch_id"]?.long(), r["created_at"].dbl())
        return m
    }

    // ---------- 调音 ----------
    fun setCalibration(site: String, wid: String, level: Int?, reasons: List<String>? = null, rnd: Int = 1, source: String = "fav") {
        if (level == null) x("DELETE FROM calibration WHERE site=? AND wid=?", site, wid)
        else x("INSERT OR REPLACE INTO calibration(site, wid, level, reasons, round, source, at) VALUES (?,?,?,?,?,?,?)",
            site, wid, level, Json.stringify(reasons ?: emptyList<String>()), rnd, source, now())
    }

    data class Cal(val level: Int, val reasons: List<String>, val round: Int, val source: String, val at: Double)

    fun calibrationMap(): LinkedHashMap<Pair<String, String>, Cal> {
        val m = LinkedHashMap<Pair<String, String>, Cal>()
        for (r in q("SELECT * FROM calibration")) m[r["site"].str() to r["wid"].str()] =
            Cal(r["level"].int(), Json.parse(r["reasons"].str().ifEmpty { "[]" }).strList(), r["round"].int(), r["source"].str(), r["at"].dbl())
        return m
    }

    // ---------- 凭据 ----------
    fun secretGet(site: String, k: String): String? = q("SELECT v FROM secrets WHERE site=? AND k=?", site, k).firstOrNull()?.get("v")?.str()

    fun secretSet(site: String, k: String, v: String?) {
        if (v == null) x("DELETE FROM secrets WHERE site=? AND k=?", site, k)
        else x("INSERT OR REPLACE INTO secrets(site, k, v) VALUES (?,?,?)", site, k, v)
    }

    companion object {
        val SCHEMA = """
CREATE TABLE IF NOT EXISTS works (site TEXT NOT NULL, wid TEXT NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]', parodies TEXT NOT NULL DEFAULT '[]', circles TEXT NOT NULL DEFAULT '[]', groups_ TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', cover TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
    popularity REAL NOT NULL DEFAULT 0, detail_ok INTEGER NOT NULL DEFAULT 0, fetched_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (site, wid));
CREATE TABLE IF NOT EXISTS collections (site TEXT NOT NULL, wid TEXT NOT NULL, rank INTEGER NOT NULL, imported_at REAL NOT NULL, PRIMARY KEY (site, wid));
CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, engine TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS batch_items (batch_id INTEGER NOT NULL, pos INTEGER NOT NULL, site TEXT NOT NULL, wid TEXT NOT NULL, score REAL NOT NULL,
    features TEXT NOT NULL, reason TEXT NOT NULL, route TEXT NOT NULL, PRIMARY KEY (batch_id, pos));
CREATE TABLE IF NOT EXISTS feedback (site TEXT NOT NULL, wid TEXT NOT NULL, label TEXT NOT NULL, batch_id INTEGER, created_at REAL NOT NULL, PRIMARY KEY (site, wid));
CREATE TABLE IF NOT EXISTS secrets (site TEXT NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, PRIMARY KEY (site, k));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calibration (site TEXT NOT NULL, wid TEXT NOT NULL, level INTEGER NOT NULL, reasons TEXT NOT NULL DEFAULT '[]',
    round INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'fav', at REAL NOT NULL, PRIMARY KEY (site, wid));
CREATE INDEX IF NOT EXISTS idx_bi_sw ON batch_items(site, wid)
"""
    }
}
