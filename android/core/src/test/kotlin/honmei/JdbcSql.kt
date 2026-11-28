package honmei

import java.sql.Connection
import java.sql.DriverManager

/** 电脑上测试用的 SQLite（sqlite-jdbc）。 */
class JdbcSql(path: String) : Sql {
    private val c: Connection = DriverManager.getConnection("jdbc:sqlite:$path")
    private var depth = 0

    private fun bind(st: java.sql.PreparedStatement, args: List<Any?>) {
        for ((i, a) in args.withIndex()) when (a) {
            null -> st.setObject(i + 1, null)
            is Boolean -> st.setInt(i + 1, if (a) 1 else 0)
            else -> st.setObject(i + 1, a)
        }
    }

    override fun query(sql: String, args: List<Any?>): List<Row> = c.prepareStatement(sql).use { st ->
        bind(st, args)
        st.executeQuery().use { rs ->
            val md = rs.metaData; val out = ArrayList<Row>()
            while (rs.next()) out.add((1..md.columnCount).associate { md.getColumnLabel(it) to rs.getObject(it) })
            out
        }
    }

    override fun exec(sql: String, args: List<Any?>) { c.prepareStatement(sql).use { bind(it, args); it.execute() } }

    override fun insert(sql: String, args: List<Any?>): Long {
        exec(sql, args)
        return c.createStatement().use { it.executeQuery("SELECT last_insert_rowid()").use { rs -> rs.next(); rs.getLong(1) } }
    }

    override fun <T> tx(block: () -> T): T {
        if (depth > 0) return block()
        c.autoCommit = false; depth++
        try { val r = block(); c.commit(); return r } catch (e: Throwable) { c.rollback(); throw e } finally { depth--; c.autoCommit = true }
    }
}
