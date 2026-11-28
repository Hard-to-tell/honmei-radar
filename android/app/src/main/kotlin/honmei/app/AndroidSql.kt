package honmei.app

import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import honmei.Row
import honmei.Sql

/** 用系统自带的 SQLite 实现 core 里的 Sql 接口。 */
class AndroidSql(path: String) : Sql {
    private val db: SQLiteDatabase = SQLiteDatabase.openOrCreateDatabase(path, null).apply { enableWriteAheadLogging() }

    private fun bindArgs(args: List<Any?>): Array<Any?> = args.map { if (it is Boolean) (if (it) 1 else 0) else it }.toTypedArray()

    override fun query(sql: String, args: List<Any?>): List<Row> {
        val strArgs: Array<String> = args.map { a -> when (a) { null -> ""; is Boolean -> if (a) "1" else "0"; else -> a.toString() } }.toTypedArray()
        // rawQuery 只收字符串参数；数字比较用的列在 SQLite 里会按亲和性自动转换
        return db.rawQuery(sql, strArgs).use { c ->
            val out = ArrayList<Row>(c.count)
            val names = c.columnNames
            while (c.moveToNext()) {
                val m = LinkedHashMap<String, Any?>(names.size)
                for (i in names.indices) m[names[i]] = when (c.getType(i)) {
                    Cursor.FIELD_TYPE_NULL -> null
                    Cursor.FIELD_TYPE_INTEGER -> c.getLong(i)
                    Cursor.FIELD_TYPE_FLOAT -> c.getDouble(i)
                    Cursor.FIELD_TYPE_BLOB -> c.getBlob(i)
                    else -> c.getString(i)
                }
                out.add(m)
            }
            out
        }
    }

    override fun exec(sql: String, args: List<Any?>) = db.execSQL(sql, bindArgs(args))

    override fun insert(sql: String, args: List<Any?>): Long {
        db.compileStatement(sql).use { st ->
            for ((i, a) in args.withIndex()) when (a) {
                null -> st.bindNull(i + 1)
                is Long -> st.bindLong(i + 1, a)
                is Int -> st.bindLong(i + 1, a.toLong())
                is Boolean -> st.bindLong(i + 1, if (a) 1 else 0)
                is Double -> st.bindDouble(i + 1, a)
                is Float -> st.bindDouble(i + 1, a.toDouble())
                is ByteArray -> st.bindBlob(i + 1, a)
                else -> st.bindString(i + 1, a.toString())
            }
            return st.executeInsert()
        }
    }

    override fun <T> tx(block: () -> T): T {
        if (db.inTransaction()) return block()
        db.beginTransaction()
        try { val r = block(); db.setTransactionSuccessful(); return r } finally { db.endTransaction() }
    }
}
