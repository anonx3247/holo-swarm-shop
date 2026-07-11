import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  CreditCard,
  LayoutDashboard,
  Minus,
  PackageCheck,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  ShoppingBag,
  SlidersHorizontal,
  Sparkles,
  Truck,
  UserRound
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type View = "shop" | "checkout" | "admin" | "quality";
type Category = "All" | "Gear" | "Home" | "Travel" | "Wellness";
type FulfillmentStatus = "New" | "Packing" | "Shipped";

type Product = {
  id: string;
  name: string;
  category: Exclude<Category, "All">;
  price: number;
  rating: number;
  inventory: number;
  accent: string;
  image: string;
  summary: string;
  tags: string[];
};

type Cart = Record<string, number>;

type Order = {
  id: string;
  customer: string;
  status: FulfillmentStatus;
  total: number;
  channel: string;
  eta: string;
};

type Bundle = {
  id: string;
  name: string;
  description: string;
  productIds: string[];
  badge: string;
};

const products: Product[] = [
  {
    id: "atlas-pack",
    name: "Atlas Daypack",
    category: "Travel",
    price: 128,
    rating: 4.9,
    inventory: 18,
    accent: "#0f766e",
    image:
      "https://images.unsplash.com/photo-1622560480605-d83c853bc5c3?auto=format&fit=crop&w=900&q=80",
    summary: "Weather-ready 22L pack with a structured laptop bay and quick-access top pocket.",
    tags: ["Carry-on", "Recycled shell", "Laptop"]
  },
  {
    id: "lumen-lamp",
    name: "Lumen Task Lamp",
    category: "Home",
    price: 84,
    rating: 4.7,
    inventory: 9,
    accent: "#a16207",
    image:
      "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=900&q=80",
    summary: "Compact dimmable desk light with warm/cool modes and a weighted ceramic base.",
    tags: ["Dimmable", "USB-C", "Low heat"]
  },
  {
    id: "summit-flask",
    name: "Summit Flask",
    category: "Gear",
    price: 38,
    rating: 4.8,
    inventory: 31,
    accent: "#2563eb",
    image:
      "https://images.unsplash.com/photo-1602143407151-7111542de6e8?auto=format&fit=crop&w=900&q=80",
    summary: "Double-wall stainless bottle that keeps trail coffee hot and sparkling water cold.",
    tags: ["Leakproof", "Insulated", "BPA-free"]
  },
  {
    id: "focus-mat",
    name: "Focus Mat",
    category: "Wellness",
    price: 64,
    rating: 4.6,
    inventory: 15,
    accent: "#7c3aed",
    image:
      "https://images.unsplash.com/photo-1601925260368-ae2f83cf8b7f?auto=format&fit=crop&w=900&q=80",
    summary: "Dense natural-rubber mat with alignment marks for stretching and mobility work.",
    tags: ["Non-slip", "Natural rubber", "6 mm"]
  },
  {
    id: "field-kit",
    name: "Field Repair Kit",
    category: "Gear",
    price: 46,
    rating: 4.5,
    inventory: 6,
    accent: "#be123c",
    image:
      "https://images.unsplash.com/photo-1585771724684-38269d6639fd?auto=format&fit=crop&w=900&q=80",
    summary: "Pocket repair roll with patches, compact driver, cord, and a tiny emergency light.",
    tags: ["Compact", "Trail-ready", "Warranty"]
  },
  {
    id: "linen-cube",
    name: "Linen Packing Cube",
    category: "Travel",
    price: 29,
    rating: 4.4,
    inventory: 42,
    accent: "#15803d",
    image:
      "https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?auto=format&fit=crop&w=900&q=80",
    summary: "Breathable organizer cube set sized for long weekends and split-compartment bags.",
    tags: ["Set of 3", "Breathable", "Machine wash"]
  }
];

const initialOrders: Order[] = [
  { id: "ORD-1048", customer: "Maya Chen", status: "New", total: 212, channel: "Web", eta: "Today" },
  { id: "ORD-1047", customer: "Noah Bell", status: "Packing", total: 93, channel: "Retail", eta: "Tomorrow" },
  { id: "ORD-1046", customer: "Ari Morgan", status: "Shipped", total: 174, channel: "Web", eta: "Jul 13" },
  { id: "ORD-1045", customer: "Priya Shah", status: "Packing", total: 64, channel: "Wholesale", eta: "Jul 14" }
];

const categories: Category[] = ["All", "Gear", "Home", "Travel", "Wellness"];
const statuses: FulfillmentStatus[] = ["New", "Packing", "Shipped"];
const bundles: Bundle[] = [
  {
    id: "weekend-reset",
    name: "Weekend Reset Kit",
    description: "A ready-to-pack set for short trips, morning coffee, and hotel-room mobility.",
    productIds: ["atlas-pack", "summit-flask", "focus-mat"],
    badge: "3 items"
  },
  {
    id: "desk-refresh",
    name: "Desk Refresh",
    description: "Small upgrades for a cleaner workspace and late-afternoon focus sessions.",
    productIds: ["lumen-lamp", "summit-flask"],
    badge: "2 items"
  }
];

function formatMoney(value: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function getStoredCart(): Cart {
  try {
    return JSON.parse(localStorage.getItem("holo-cart") || "{}") as Cart;
  } catch {
    return {};
  }
}

function App() {
  const [view, setView] = useState<View>("shop");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category>("All");
  const [cart, setCart] = useState<Cart>(getStoredCart);
  const [promo, setPromo] = useState("");
  const [orders, setOrders] = useState<Order[]>(initialOrders);
  const [checkoutStep, setCheckoutStep] = useState(1);
  const [toast, setToast] = useState("Preview-ready no-backend app loaded.");

  useEffect(() => {
    localStorage.setItem("holo-cart", JSON.stringify(cart));
  }, [cart]);

  const filteredProducts = useMemo(() => {
    return products.filter((product) => {
      const matchesQuery = [product.name, product.summary, product.category, ...product.tags]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase());
      return matchesQuery && (category === "All" || product.category === category);
    });
  }, [category, query]);

  const cartItems = products
    .map((product) => ({ product, quantity: cart[product.id] || 0 }))
    .filter((item) => item.quantity > 0);
  const subtotal = cartItems.reduce((sum, item) => sum + item.product.price * item.quantity, 0);
  const discount = promo.trim().toUpperCase() === "HOLO15" ? subtotal * 0.15 : 0;
  const shipping = subtotal > 150 || subtotal === 0 ? 0 : 12;
  const total = Math.max(0, subtotal - discount + shipping);

  function updateCart(productId: string, delta: number) {
    setCart((current) => {
      const nextQuantity = Math.max(0, (current[productId] || 0) + delta);
      const next = { ...current, [productId]: nextQuantity };
      if (nextQuantity === 0) {
        delete next[productId];
      }
      return next;
    });
  }

  function addBundle(bundle: Bundle) {
    setCart((current) => {
      const next = { ...current };
      bundle.productIds.slice(0, 1).forEach((productId) => {
        next[productId] = (next[productId] || 0) + 1;
      });
      return next;
    });
    setToast(`${bundle.name} added to cart.`);
  }

  function submitCheckout() {
    if (!cartItems.length) {
      setToast("Add at least one product before checkout.");
      return;
    }
    const nextId = `ORD-${1049 + orders.length}`;
    setOrders((current) => [
      {
        id: nextId,
        customer: "Preview Shopper",
        status: "New",
        total,
        channel: "Web",
        eta: "Today"
      },
      ...current
    ]);
    setCart({});
    setCheckoutStep(3);
    setToast(`Order ${nextId} created locally for this browser.`);
  }

  function advanceOrder(orderId: string) {
    setOrders((current) =>
      current.map((order) => {
        if (order.id !== orderId) return order;
        const nextStatus = order.status === "New" ? "Packing" : order.status === "Packing" ? "Shipped" : "Shipped";
        return { ...order, status: nextStatus };
      })
    );
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">H</span>
          <div>
            <strong>Holo Swarm Shop</strong>
            <span>PR preview target</span>
          </div>
        </div>
        <nav aria-label="Primary">
          <button className={view === "shop" ? "active" : ""} onClick={() => setView("shop")}>
            <ShoppingBag size={18} /> Storefront
          </button>
          <button className={view === "checkout" ? "active" : ""} onClick={() => setView("checkout")}>
            <CreditCard size={18} /> Checkout
          </button>
          <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}>
            <LayoutDashboard size={18} /> Admin
          </button>
          <button className={view === "quality" ? "active" : ""} onClick={() => setView("quality")}>
            <ShieldCheck size={18} /> QA Surface
          </button>
        </nav>
        <div className="sidebar-summary">
          <span>Cart total</span>
          <strong>{formatMoney(total)}</strong>
          <button onClick={() => setView("checkout")}>
            Review cart <ArrowRight size={16} />
          </button>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Static Vercel demo</p>
            <h1>{viewTitle(view)}</h1>
          </div>
          <div className="topbar-actions">
            <span className="toast" role="status">
              {toast}
            </span>
            <button className="ghost" onClick={() => setToast("All state is local to this browser.")}>
              <UserRound size={17} /> Preview user
            </button>
          </div>
        </header>

        {view === "shop" && (
          <Storefront
            query={query}
            setQuery={setQuery}
            category={category}
            setCategory={setCategory}
            products={filteredProducts}
            bundles={bundles}
            cart={cart}
            updateCart={updateCart}
            addBundle={addBundle}
          />
        )}
        {view === "checkout" && (
          <Checkout
            cartItems={cartItems}
            subtotal={subtotal}
            shipping={shipping}
            discount={discount}
            total={total}
            promo={promo}
            setPromo={setPromo}
            updateCart={updateCart}
            step={checkoutStep}
            setStep={setCheckoutStep}
            submitCheckout={submitCheckout}
          />
        )}
        {view === "admin" && <Admin orders={orders} products={products} advanceOrder={advanceOrder} />}
        {view === "quality" && <QualitySurface setView={setView} />}
      </section>
    </main>
  );
}

function viewTitle(view: View) {
  if (view === "checkout") return "Checkout Console";
  if (view === "admin") return "Operations Board";
  if (view === "quality") return "Regression Surface";
  return "Storefront";
}

function Storefront({
  query,
  setQuery,
  category,
  setCategory,
  products: visibleProducts,
  bundles: visibleBundles,
  cart,
  updateCart,
  addBundle
}: {
  query: string;
  setQuery: (query: string) => void;
  category: Category;
  setCategory: (category: Category) => void;
  products: Product[];
  bundles: Bundle[];
  cart: Cart;
  updateCart: (productId: string, delta: number) => void;
  addBundle: (bundle: Bundle) => void;
}) {
  return (
    <>
      <section className="hero">
        <img
          src="https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?auto=format&fit=crop&w=1400&q=80"
          alt="Outdoor lifestyle products arranged for travel"
        />
        <div className="hero-copy">
          <p>Preview store</p>
          <h2>Shop the release, then break it like a reviewer.</h2>
          <span>Search, filter, add to cart, apply HOLO15, and complete checkout without a backend.</span>
        </div>
      </section>

      <section className="toolbar" aria-label="Product filters">
        <label className="search">
          <Search size={18} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search products, tags, categories" />
        </label>
        <div className="category-tabs">
          {categories.map((item) => (
            <button key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>
              {item}
            </button>
          ))}
        </div>
      </section>

      <section className="bundle-strip" aria-label="Curated bundles">
        {visibleBundles.map((bundle) => (
          <article className="bundle-card" key={bundle.id}>
            <div>
              <span>{bundle.badge}</span>
              <h3>{bundle.name}</h3>
              <p>{bundle.description}</p>
            </div>
            <button className="primary" onClick={() => addBundle(bundle)}>
              Add bundle <Plus size={16} />
            </button>
          </article>
        ))}
      </section>

      <section className="product-grid" aria-label="Products">
        {visibleProducts.map((product) => (
          <article className="product-card" key={product.id} style={{ "--accent": product.accent } as React.CSSProperties}>
            <img src={product.image} alt={product.name} />
            <div className="product-body">
              <div className="product-meta">
                <span>{product.category}</span>
                <strong>{product.rating.toFixed(1)}</strong>
              </div>
              <h3>{product.name}</h3>
              <p>{product.summary}</p>
              <div className="tag-row">
                {product.tags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
              <div className="product-footer">
                <div>
                  <strong>{formatMoney(product.price)}</strong>
                  <span>{product.inventory} in stock</span>
                </div>
                <div className="quantity-control" aria-label={`${product.name} quantity`}>
                  <button onClick={() => updateCart(product.id, -1)} aria-label={`Remove ${product.name}`}>
                    <Minus size={15} />
                  </button>
                  <output>{cart[product.id] || 0}</output>
                  <button onClick={() => updateCart(product.id, 1)} aria-label={`Add ${product.name}`}>
                    <Plus size={15} />
                  </button>
                </div>
              </div>
            </div>
          </article>
        ))}
      </section>
    </>
  );
}

function Checkout({
  cartItems,
  subtotal,
  shipping,
  discount,
  total,
  promo,
  setPromo,
  updateCart,
  step,
  setStep,
  submitCheckout
}: {
  cartItems: { product: Product; quantity: number }[];
  subtotal: number;
  shipping: number;
  discount: number;
  total: number;
  promo: string;
  setPromo: (promo: string) => void;
  updateCart: (productId: string, delta: number) => void;
  step: number;
  setStep: (step: number) => void;
  submitCheckout: () => void;
}) {
  return (
    <section className="checkout-layout">
      <div className="checkout-main">
        <div className="stepper" aria-label="Checkout steps">
          {["Cart", "Delivery", "Confirmation"].map((label, index) => (
            <button key={label} className={step === index + 1 ? "active" : ""} onClick={() => setStep(index + 1)}>
              {index + 1}
              <span>{label}</span>
            </button>
          ))}
        </div>

        {step === 1 && (
          <div className="panel">
            <h2>Cart Review</h2>
            {cartItems.length === 0 ? (
              <p className="empty">Your cart is empty. Add products from the storefront to continue.</p>
            ) : (
              cartItems.map((item) => (
                <div className="cart-line" key={item.product.id}>
                  <img src={item.product.image} alt="" />
                  <div>
                    <strong>{item.product.name}</strong>
                    <span>{formatMoney(item.product.price)} each</span>
                  </div>
                  <div className="quantity-control">
                    <button onClick={() => updateCart(item.product.id, -1)} aria-label={`Remove ${item.product.name}`}>
                      <Minus size={15} />
                    </button>
                    <output>{item.quantity}</output>
                    <button onClick={() => updateCart(item.product.id, 1)} aria-label={`Add ${item.product.name}`}>
                      <Plus size={15} />
                    </button>
                  </div>
                </div>
              ))
            )}
            <button className="primary" onClick={() => setStep(2)}>
              Continue to delivery <ArrowRight size={17} />
            </button>
          </div>
        )}

        {step === 2 && (
          <div className="panel">
            <h2>Delivery Details</h2>
            <div className="form-grid">
              <label>
                Name
                <input defaultValue="Preview Shopper" />
              </label>
              <label>
                Email
                <input defaultValue="preview@example.com" />
              </label>
              <label className="wide">
                Shipping address
                <input defaultValue="321 Regression Ave, San Francisco, CA" />
              </label>
              <label>
                Delivery window
                <select defaultValue="morning">
                  <option value="morning">Tomorrow morning</option>
                  <option value="afternoon">Tomorrow afternoon</option>
                  <option value="pickup">Store pickup</option>
                </select>
              </label>
              <label>
                Payment
                <select defaultValue="card">
                  <option value="card">Card ending 4242</option>
                  <option value="wallet">Express wallet</option>
                </select>
              </label>
            </div>
            <button className="primary" onClick={submitCheckout}>
              Place local order <PackageCheck size={17} />
            </button>
          </div>
        )}

        {step === 3 && (
          <div className="confirmation panel">
            <CheckCircle2 size={44} />
            <h2>Ready for QA</h2>
            <p>The checkout path completed and created a browser-local order. Agents can verify this without any backend service.</p>
            <button className="primary" onClick={() => setStep(1)}>
              Review another cart <RotateCcw size={17} />
            </button>
          </div>
        )}
      </div>

      <aside className="summary-panel">
        <h2>Order Summary</h2>
        <label className="promo">
          Promo code
          <input value={promo} onChange={(event) => setPromo(event.target.value)} placeholder="Try HOLO15" />
        </label>
        <dl>
          <div>
            <dt>Subtotal</dt>
            <dd>{formatMoney(subtotal)}</dd>
          </div>
          <div>
            <dt>Discount</dt>
            <dd>-{formatMoney(discount)}</dd>
          </div>
          <div>
            <dt>Shipping</dt>
            <dd>{shipping === 0 ? "Free" : formatMoney(shipping)}</dd>
          </div>
          <div className="total">
            <dt>Total</dt>
            <dd>{formatMoney(total)}</dd>
          </div>
        </dl>
      </aside>
    </section>
  );
}

function Admin({
  orders,
  products: allProducts,
  advanceOrder
}: {
  orders: Order[];
  products: Product[];
  advanceOrder: (orderId: string) => void;
}) {
  const revenue = orders.reduce((sum, order) => sum + order.total, 0);
  const lowStock = allProducts.filter((product) => product.inventory <= 10);

  return (
    <section className="admin-layout">
      <div className="metric-row">
        <Metric icon={<ClipboardList size={20} />} label="Open orders" value={orders.filter((order) => order.status !== "Shipped").length.toString()} />
        <Metric icon={<Truck size={20} />} label="Ready to ship" value={orders.filter((order) => order.status === "Packing").length.toString()} />
        <Metric icon={<Sparkles size={20} />} label="Preview revenue" value={formatMoney(revenue)} />
      </div>

      <div className="board">
        {statuses.map((status) => (
          <section className="board-column" key={status}>
            <header>
              <h2>{status}</h2>
              <span>{orders.filter((order) => order.status === status).length}</span>
            </header>
            {orders
              .filter((order) => order.status === status)
              .map((order) => (
                <article className="order-card" key={order.id}>
                  <div>
                    <strong>{order.id}</strong>
                    <span>{order.customer}</span>
                  </div>
                  <dl>
                    <div>
                      <dt>Total</dt>
                      <dd>{formatMoney(order.total)}</dd>
                    </div>
                    <div>
                      <dt>ETA</dt>
                      <dd>{order.eta}</dd>
                    </div>
                    <div>
                      <dt>Channel</dt>
                      <dd>{order.channel}</dd>
                    </div>
                  </dl>
                  <button onClick={() => advanceOrder(order.id)} disabled={order.status === "Shipped"}>
                    Advance <ArrowRight size={15} />
                  </button>
                </article>
              ))}
          </section>
        ))}
      </div>

      <section className="inventory-panel">
        <header>
          <div>
            <p className="eyebrow">Inventory</p>
            <h2>Low-stock watchlist</h2>
          </div>
          <button className="ghost">
            <SlidersHorizontal size={17} /> Filters
          </button>
        </header>
        {lowStock.map((product) => (
          <div className="inventory-line" key={product.id}>
            <span style={{ background: product.accent }} />
            <strong>{product.name}</strong>
            <em>{product.inventory} units</em>
          </div>
        ))}
      </section>
    </section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <article className="metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function QualitySurface({ setView }: { setView: (view: View) => void }) {
  return (
    <section className="quality-grid">
      {[
        {
          title: "Promo Recalculation",
          text: "Apply HOLO15 during checkout and verify the total updates immediately.",
          action: "Open checkout",
          view: "checkout" as View
        },
        {
          title: "Inventory Visibility",
          text: "Review low-stock products and compare storefront stock counts.",
          action: "Open admin",
          view: "admin" as View
        },
        {
          title: "Responsive Storefront",
          text: "Use mobile and desktop widths to check navigation, product cards, and cart controls.",
          action: "Open store",
          view: "shop" as View
        }
      ].map((item) => (
        <article className="quality-card" key={item.title}>
          <div className="quality-icon">
            <ChevronDown size={20} />
          </div>
          <h2>{item.title}</h2>
          <p>{item.text}</p>
          <button className="primary" onClick={() => setView(item.view)}>
            {item.action} <ArrowRight size={17} />
          </button>
        </article>
      ))}
    </section>
  );
}

export default App;
