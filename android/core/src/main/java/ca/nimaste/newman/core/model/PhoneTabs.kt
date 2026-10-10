package ca.nimaste.newman.core.model

/**
 * Which dashboard pages become the phone's bottom tabs. The phone has five slots: the first three
 * pages (Overview, Security, Lights on the starter dashboard), then People and Alerts. A "Today"
 * page (greeting / To do / calendar / weather - the wall's morning page) isn't a tab: the phone
 * shows its own Today card at the top of the first tab. Pages beyond the third (Climate) open from
 * their thermostat tile, or from a link at the bottom of the first tab.
 */
object PhoneTabs {
    const val PAGE_TABS = 3

    /** Tile types that make up a Today page, and the information tiles it may also hold. */
    val TODAY_TYPES = setOf("greeting", "todo", "today")
    private val TODAY_MARKERS = setOf("greeting", "todo")
    private val INFO_TYPES = setOf("calendar", "weather")

    data class Plan(
        /** Pages shown as tabs, in dashboard order. */
        val tabs: List<Page>,
        /** The dashboard's Today page, if it has one (shown as the Today card instead). */
        val today: Page?,
        /** Pages that didn't get a tab (opened from a dial tile or a link). */
        val more: List<Page>,
        /** From a phone layout (PhoneGrid): tiles sit at their own x/y/w/h instead of flowing. */
        val explicit: Boolean = false,
        /** Phone layouts: the layout asks for the Today card on the first tab. */
        val todayCard: Boolean = false,
    )

    /** Whether the first tab shows the Today card above [page]'s tiles. */
    fun showsTodayCard(plan: Plan, page: Page): Boolean = if (plan.explicit) plan.todayCard else !hasTodayTile(page)

    /** Whether that card stays up on a day with nothing in it (it was asked for). */
    fun todayWhenEmpty(plan: Plan): Boolean = plan.explicit || plan.today != null

    fun isTodayPage(p: Page): Boolean =
        p.tiles.isNotEmpty() && p.tiles.any { it.type in TODAY_MARKERS } && p.tiles.all { it.type in TODAY_TYPES || it.type in INFO_TYPES }

    fun plan(pages: List<Page>): Plan {
        val today = pages.firstOrNull(::isTodayPage)
        val rest = pages.filter { it !== today }
        return Plan(rest.take(PAGE_TABS), today, rest.drop(PAGE_TABS))
    }

    /** The page a compact dial tile opens: a page without a tab holding a dial for the same device. */
    fun pageForDial(plan: Plan, entityId: String?): Page? {
        if (entityId == null) return null
        return plan.more.firstOrNull { p -> p.tiles.any { it.type == "dial" && it.config.string("entity") == entityId } }
    }

    /** True when [page] already shows today's briefing through a tile of its own. */
    fun hasTodayTile(page: Page): Boolean = page.tiles.any { it.type == "today" || it.type == "todo" }
}
