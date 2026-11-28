package honmei

import java.io.File

/** 运行环境：数据目录 + 数据库。安卓端在 Application 里初始化一次。 */
object Env {
    lateinit var dataDir: File
    lateinit var db: DB
    var log: (String) -> Unit = { System.err.println(it) }

    fun init(dir: File, db: DB) {
        dataDir = dir.apply { mkdirs() }
        this.db = db
        // 跨站学到的标签对照、用户手动的对照
        Norm.learnedAliases = readJsonMap(File(dir, "learned_aliases.json"))
        Norm.userAliases = readJsonMap(File(dir, "tag_aliases.json"))
    }

    fun readJsonMap(f: File): Map<String, String> = try {
        if (f.exists()) Json.parse(f.readText()).obj().mapValues { it.value.str() } else emptyMap()
    } catch (e: Exception) { emptyMap() }

    fun logLine(msg: String) {
        try {
            val f = File(dataDir, "app.log")
            if (f.length() > 2_000_000) f.renameTo(File(dataDir, "app.log.1"))
            f.appendText(java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(java.util.Date()) + " " + msg + "\n")
        } catch (_: Exception) {}
        log(msg)
    }
}

/** 本地配置（对应 config.py；存成数据目录里的 settings.json，只写改过的项）。 */
object Settings {
    val DEFAULTS: Map<String, Any?> = linkedMapOf(
        "batch_size" to 10L,
        "explore_ratio" to 0.15,
        "max_per_author" to 2L,
        "rec_mode" to "balance",
        "blocked_tags" to emptyList<String>(),
        "card_anim" to true,
        "proxy" to "",
        "jm" to linkedMapOf("web_domain" to "18comic.vip", "api_domains" to emptyList<String>()),
        "pica" to linkedMapOf("web_url_template" to "https://manhuabika.com/pcomicview/?cid={id}"),
        "rate_interval" to 0.15,
    )

    private fun file() = File(Env.dataDir, "settings.json")

    @Suppress("UNCHECKED_CAST")
    fun merge(base: Map<String, Any?>, over: Map<String, Any?>): MutableMap<String, Any?> {
        val out = LinkedHashMap(base)
        for ((k, v) in over) {
            val b = out[k]
            out[k] = if (v is Map<*, *> && b is Map<*, *>) merge(b as Map<String, Any?>, v as Map<String, Any?>) else v
        }
        return out
    }

    private fun raw(): Map<String, Any?> = try {
        val f = file(); if (f.exists()) Json.parse(f.readText()).obj() else emptyMap()
    } catch (e: Exception) { emptyMap() }

    @Synchronized fun load(): Map<String, Any?> = merge(DEFAULTS, raw())

    @Synchronized fun save(patch: Map<String, Any?>): Map<String, Any?> {
        val r = merge(raw(), patch)
        file().writeText(Json.stringify(r, 2))
        return merge(DEFAULTS, r)
    }
}
