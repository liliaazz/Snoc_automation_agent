"use client";

import {
  AnimatePresence,
  motion,
  useAnimationControls,
} from "framer-motion";

import {
  ChartNoAxesCombined,
  CircleUserRound,
  FileClock,
  Home,
  ListTree,
  LogOut,
  Mail,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Users,
  Workflow,
} from "lucide-react";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "../i18n/useTranslation";
import { isAdmin } from "../utils/permissions";

// Same nav shape as the original ESI Logis Side.jsx (icon / text / href),
// now wired to the 8 required SNOC pages. Account Management is filtered
// out entirely for non-admins — it never renders in the DOM for them.
const NAV_ITEMS = [
  {
    id: "home",
    icon: Home,
    labelKey: "nav.home",
  },
  {
    id: "emails",
    icon: Mail,
    labelKey: "nav.emails",
  },
  {
    id: "audit",
    icon: FileClock,
    labelKey: "nav.audit",
  },
  {
    id: "bizanalysis",
    icon: ChartNoAxesCombined,
    labelKey: "nav.bizanalysis",
  },
  {
    id: "opanalysis",
    icon: Workflow,
    labelKey: "nav.opanalysis",
  },
  {
    id: "configuration",
    icon: Settings,
    labelKey: "nav.configuration",
  },
  {
    id: "parametre",
    icon: ListTree,
    labelKey: "nav.parametre",
  },
  {
    id: "accounts",
    icon: Users,
    labelKey: "nav.accounts",
    adminOnly: true,
  },
];

export default function Sidebar({
  activePage,
  onChange,
  currentUser,
  onRequestLogout,
}) {
  const { t } = useTranslation();

  const [isOpen, setIsOpen] = useState(false);
  const [isSidebarFullyOpen, setIsSidebarFullyOpen] =
    useState(false);

  const [hoveredIndex, setHoveredIndex] = useState(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  const controls = useAnimationControls();
  const itemRefs = useRef([]);

  const visibleItems = NAV_ITEMS.filter(
    (item) =>
      !item.adminOnly || isAdmin(currentUser.role),
  );

  useEffect(() => {
    if (isOpen) {
      controls
        .start({
          width: 250,
        })
        .then(() => {
          setIsSidebarFullyOpen(true);
        });
    } else {
      setIsSidebarFullyOpen(false);

      controls.start({
        width: 130,
      });
    }
  }, [isOpen, controls]);

  function navigate(id) {
    window.location.hash = id;
    onChange(id);
    setMobileOpen(false);
  }

  const expanded = isOpen || mobileOpen;

  return (
    <>
      {/* Mobile navigation button */}
      <button
        type="button"
        className="
          fixed left-3 top-3 z-[95]
          grid h-10 w-10 place-items-center
          rounded-[10px]
          border border-[#292C31]
          bg-[#0B0D0F]
          text-white
          shadow-lg
          transition-colors duration-200
          hover:border-[#F20521]
          hover:bg-[#17191C]
          focus:outline-none
          focus:ring-2
          focus:ring-[#F20521]
          focus:ring-offset-2
          md:hidden
        "
        onClick={() =>
          setMobileOpen((value) => !value)
        }
        aria-label="Toggle navigation"
      >
        {mobileOpen ? (
          <PanelLeftClose size={20} />
        ) : (
          <PanelLeftOpen size={20} />
        )}
      </button>

      <motion.nav
        className={`
          fixed left-1 top-1 z-50
          flex flex-col
          overflow-hidden
          rounded-2xl
          border border-[#292C31]
          bg-[#0B0D0F]
          shadow-[0_12px_35px_rgba(0,0,0,0.42)]
          transition-transform duration-300

          ${
            mobileOpen
              ? "translate-x-0"
              : "-translate-x-[120%]"
          }

          md:translate-x-0
        `}
        style={{
          height: "calc(100vh - 8px)",
        }}
        role="navigation"
        initial={{
          width: 130,
        }}
        animate={
          mobileOpen
            ? {
                width: 250,
              }
            : controls
        }
        transition={{
          duration: 0.3,
        }}
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => {
          setIsOpen(false);
          setHoveredIndex(null);
        }}
      >
        {/* Text logo */}
        <div
          className={`
            flex min-h-[105px] w-full
            items-center

            ${
              expanded
                ? "justify-start px-5"
                : "justify-center px-0"
            }
          `}
        >
          <motion.div
            className="
              flex items-center justify-center
              whitespace-nowrap
            "
            initial={{
              opacity: 0,
              scale: 0.92,
            }}
            animate={{
              opacity: 1,
              scale: 1,
            }}
            transition={{
              duration: 0.35,
            }}
          >
            {/* Red status dot */}
            <motion.span
              className="
                mr-3 h-[10px] w-[10px]
                shrink-0 rounded-full
                bg-[#F20521]
                shadow-[0_0_12px_rgba(242,5,33,0.55)]
              "
              animate={{
                opacity: [1, 0.65, 1],
                scale: [1, 0.9, 1],
              }}
              transition={{
                duration: 2,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />

            <div
              className={`
                overflow-hidden
                transition-[width] duration-300

                ${
                  expanded
                    ? "w-[175px]"
                    : "w-auto"
                }
              `}
            >
              <div className="flex items-baseline whitespace-nowrap">
                <span
                  className="
                    font-oxanium
                    text-[14px]
                    font-extrabold
                    tracking-[0.12em]
                    text-white
                  "
                >
                  SNOC
                </span>

                <AnimatePresence>
                  {expanded && (
                    <motion.span
                      initial={{
                        opacity: 0,
                        x: -8,
                      }}
                      animate={{
                        opacity: 1,
                        x: 0,
                      }}
                      exit={{
                        opacity: 0,
                        x: -8,
                      }}
                      transition={{
                        duration: 0.2,
                      }}
                      className="
                        ml-1.5
                        font-oxanium
                        text-[8px]
                        font-semibold
                        tracking-[0.2em]
                        text-[#767C86]
                      "
                    >
                      AI AGENT
                    </motion.span>
                  )}
                </AnimatePresence>
              </div>

              <AnimatePresence>
                {expanded && (
                  <motion.div
                    initial={{
                      opacity: 0,
                      x: -8,
                    }}
                    animate={{
                      opacity: 1,
                      x: 0,
                    }}
                    exit={{
                      opacity: 0,
                      x: -8,
                    }}
                    transition={{
                      duration: 0.2,
                    }}
                    className="
                      mt-1 whitespace-nowrap
                      font-oxanium
                      text-[7px]
                      font-semibold
                      tracking-[0.18em]
                      text-[#A4A9B1]
                    "
                  >
                    DIGITAL TECH SUPPORT
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        </div>

        {/* Divider */}
        <div className="flex w-full items-center justify-center px-4">
          <motion.div
            className="h-px bg-[#25282D]"
            initial={{
              width: "50%",
            }}
            animate={{
              width: expanded ? "100%" : "50%",
            }}
            transition={{
              duration: 0.3,
            }}
          />
        </div>

        {/* Navigation items */}
        <ul
          className={`
            flex w-full flex-1
            flex-col
            overflow-y-auto
            py-3
            scrollbar-thin
            scrollbar-track-transparent
            scrollbar-thumb-[#292C31]

            ${
              expanded
                ? "items-stretch px-3"
                : "items-center px-0"
            }
          `}
        >
          {visibleItems.map((item, index) => {
            const Icon = item.icon;
            const active =
              activePage === item.id;

            const hovered =
              hoveredIndex === index;

            return (
              <li
                key={item.id}
                ref={(element) => {
                  itemRefs.current[index] = element;
                }}
                className={`
                  relative flex py-1

                  ${
                    expanded
                      ? "w-full justify-start"
                      : "w-full justify-center"
                  }
                `}
                onMouseEnter={() =>
                  setHoveredIndex(index)
                }
                onMouseLeave={() =>
                  setHoveredIndex(null)
                }
              >
                <motion.button
                  type="button"
                  onClick={() =>
                    navigate(item.id)
                  }
                  aria-current={
                    active
                      ? "page"
                      : undefined
                  }
                  title={
                    !expanded
                      ? t(item.labelKey)
                      : undefined
                  }
                  className={`
                    relative
                    flex items-center
                    overflow-hidden
                    rounded-[11px]
                    py-3
                    transition-all duration-200

                    ${
                      expanded
                        ? "w-full justify-start gap-3 px-3 text-left"
                        : "h-12 w-[72px] justify-center gap-0 px-0 text-center"
                    }

                    ${
                      active
                        ? "bg-[#F20521] text-white shadow-[0_7px_20px_rgba(242,5,33,0.22)]"
                        : "bg-transparent text-[#A4A9B1] hover:bg-[#17191C] hover:text-white"
                    }
                  `}
                  whileTap={{
                    scale: 0.97,
                  }}
                >
                  {/* Icon */}
                  <div
                    className="
                      flex h-[22px] w-[22px]
                      shrink-0
                      items-center justify-center
                    "
                  >
                    <motion.div
                      className="
                        flex h-full w-full
                        items-center justify-center
                      "
                      animate={{
                        scale:
                          hovered || active
                            ? 1.08
                            : 1,
                      }}
                      transition={{
                        duration: 0.18,
                      }}
                    >
                      <Icon
                        size={19}
                        strokeWidth={1.9}
                        className={`
                          block h-[19px] w-[19px]
                          transition-colors duration-200

                          ${
                            active || hovered
                              ? "text-white"
                              : "text-[#A4A9B1]"
                          }
                        `}
                      />
                    </motion.div>
                  </div>

                  {/* Navigation label */}
                  <AnimatePresence>
                    {expanded && (
                      <motion.span
                        initial={{
                          opacity: 0,
                          x: -18,
                        }}
                        animate={{
                          opacity: 1,
                          x: 0,
                        }}
                        exit={{
                          opacity: 0,
                          x: -18,
                        }}
                        transition={{
                          duration: 0.12,
                        }}
                        className={`
                          whitespace-nowrap
                          font-oxanium
                          text-xs font-semibold
                          sm:text-sm

                          ${
                            active || hovered
                              ? "text-white"
                              : "text-[#C7CAD0]"
                          }
                        `}
                      >
                        {t(item.labelKey)}
                      </motion.span>
                    )}
                  </AnimatePresence>

                  {/* Active border */}
                  {active && (
                    <motion.span
                      className="
                        pointer-events-none
                        absolute inset-0
                        rounded-[11px]
                        border border-white/10
                      "
                      initial={{
                        opacity: 0,
                      }}
                      animate={{
                        opacity: 1,
                      }}
                      transition={{
                        duration: 0.2,
                      }}
                    />
                  )}
                </motion.button>
              </li>
            );
          })}
        </ul>

        {/* Bottom profile info */}
        <div
          className={`
            mt-auto
            flex w-full flex-col
            justify-center
            border-t border-[#25282D]
            p-3
            sm:p-4

            ${
              expanded
                ? "items-start"
                : "items-center"
            }
          `}
        >
          <div
            className={`
              flex min-h-12 w-full
              flex-row items-center

              ${
                expanded
                  ? "justify-start"
                  : "justify-center"
              }
            `}
          >
            {/* User avatar */}
            <div
              className="
                flex h-10 w-10
                shrink-0
                items-center justify-center
                rounded-[9px]
                bg-[#F20521]
                text-white
                shadow-[0_5px_15px_rgba(242,5,33,0.2)]
              "
            >
              {currentUser?.name ? (
                <span className="font-outfit text-[11px] font-bold">
                  {currentUser.name
                    .split(" ")
                    .map((part) =>
                      part.charAt(0),
                    )
                    .join("")
                    .slice(0, 2)
                    .toUpperCase()}
                </span>
              ) : (
                <CircleUserRound
                  size={23}
                  strokeWidth={1.7}
                />
              )}
            </div>

            {/* Profile name and role */}
            <AnimatePresence>
              {expanded && (
                <motion.div
                  initial={{
                    opacity: 0,
                    x: -12,
                  }}
                  animate={{
                    opacity: 1,
                    x: 0,
                  }}
                  exit={{
                    opacity: 0,
                    x: -12,
                  }}
                  transition={{
                    duration: 0.18,
                  }}
                  className="
                    ml-3 flex min-w-0
                    flex-1 flex-col
                    justify-center
                  "
                >
                  <h1
                    className="
                      truncate whitespace-nowrap
                      font-outfit
                      text-[12px]
                      font-semibold
                      text-white
                    "
                  >
                    {currentUser.name}
                  </h1>

                  <span
                    className="
                      block truncate
                      whitespace-nowrap
                      font-outfit
                      text-[9px]
                      font-light
                      text-[#858B94]
                    "
                  >
                    {t(
                      isAdmin(
                        currentUser.role,
                      )
                        ? "role.admin"
                        : "role.user",
                    )}
                  </span>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Logout button */}
            <AnimatePresence>
              {isSidebarFullyOpen && (
                <motion.button
                  type="button"
                  onClick={onRequestLogout}
                  aria-label={t(
                    "btn.logout",
                  )}
                  title={t("btn.logout")}
                  className="
                    ml-2
                    grid h-9 w-9
                    shrink-0 place-items-center
                    rounded-[9px]
                    border border-[#34383E]
                    bg-[#15171A]
                    text-[#A4A9B1]
                    transition-colors duration-200
                    hover:border-[#F20521]
                    hover:bg-[#F20521]
                    hover:text-white
                    focus:outline-none
                    focus:ring-2
                    focus:ring-[#F20521]/40
                  "
                  initial={{
                    opacity: 0,
                    x: 10,
                    scale: 0.9,
                  }}
                  animate={{
                    opacity: 1,
                    x: 0,
                    scale: 1,
                  }}
                  exit={{
                    opacity: 0,
                    x: 5,
                    scale: 0.95,
                  }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 20,
                    delay: 0.1,
                  }}
                  whileHover={{
                    scale: 1.05,
                  }}
                  whileTap={{
                    scale: 0.95,
                  }}
                >
                  <LogOut size={16} />
                </motion.button>
              )}
            </AnimatePresence>
          </div>

        </div>
      </motion.nav>

      {/* Mobile backdrop */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="
            fixed inset-0 z-40
            border-0
            bg-black/65
            backdrop-blur-[2px]
            md:hidden
          "
          onClick={() =>
            setMobileOpen(false)
          }
        />
      )}
    </>
  );
}

export { NAV_ITEMS };