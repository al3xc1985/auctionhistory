import datetime
import sqlite3 as sqlite
import re
from statistics import median

from flask import render_template, request, redirect, abort
import plotly.offline as plotly
import plotly.graph_objs as plotgo
import pandas as pd
import numpy as np

from app import app


def copper_to_price(copper):
    '''Return the price in WoW currency.'''
    copper = int(copper)
    s, c = divmod(copper, 100)
    g, s = divmod(s, 100)
    if g == 0:
        if s == 0:
            return '{}c'.format(c)
        else:
            return '{}s{}c'.format(s, c)
    else:
        return '{}g{}s{}c'.format(g, s, c)


def get_date(unix_time):
    '''Return date in dd-mm-yyyy.'''
    return datetime.datetime.fromtimestamp(unix_time).strftime('%d-%m-%y')


def write_log(realm, search, reply, range=None):
    '''Logs user searches'''
    ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
    time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open('log.csv', 'a') as log:
        log.write(f'{time},{ip},{realm},{search},{range},{reply}\n')

con = sqlite.connect('file:import/auctionhistory.db?mode=ro', uri=True) #db path
cur = con.cursor()

REALMS = {
        "warmane": [
            "Lordaeron_Alliance",
            "Lordaeron_Horde",
            "Icecrown_Alliance",
            "Icecrown_Horde"
        ],
        "gamerdistrict": [
            "Echoes_1x_Alliance",
            "Echoes_1x_Horde"
        ]
}

CAP = {"warmane": "Warmane",
       "gamerdistrict": "GamerDistrict"
}

@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html', title='Warmane & Gamer District')


@app.route('/server/<_i>')
def legacy(_i):
    url = (request.full_path).replace('server', 'warmane')
    return redirect(url, code=301)


@app.route('/<server_arg>/<realm_arg>', methods=['GET'])
def search(server_arg, realm_arg):
    try:
        if realm_arg in REALMS[server_arg]:
            pass
    except KeyError:
        abort(404)
    search_arg = request.args.get('search', '')
    time_arg = request.args.get('time', None)
    AH_title = f"{realm_arg.replace('_', ' ')} Auction House Price History"
    tab = f"{CAP[server_arg]}: {realm_arg.replace('_', ' ')}"
    html_page = 'search.html'
    epoch_now = int(datetime.datetime.now().timestamp())
    time_dir = {'30d': 2592000,
                '3m': 7890000,
                '1y': 31536000,
                'all': epoch_now}
    try: 
        scantime = epoch_now - time_dir[time_arg]
    except KeyError:
        scantime = None
    
    if not search_arg or scantime == None:
        return render_template(html_page, title=tab, AH_title=AH_title,
                               tvalue='3m')
    query = (search_arg, scantime)
    cur.execute("SELECT itemid FROM WOTLK_items "
                "WHERE itemname IS ? ;", (search_arg,))
    
    if not cur.fetchone():  # no direct match
        if len(search_arg) < 3:
            return render_template(html_page, title='Item not found',
                                   AH_title=AH_title, 
                                   error='Type at least 3 characters',
                                   value=search_arg, tvalue=time_arg)
        
        cur.execute("SELECT itemname FROM WOTLK_items WHERE itemname LIKE ?;",
                   ('{0}{search}{0}'.format('%', search=query[0]),))
        item_matches = sorted(cur.fetchall(), key=lambda x: len(x[0]))
        if item_matches:
            write_log(realm_arg, search_arg, 'suggest')
            item_suggestions = []
            for match in item_matches:
                href_display = match[0]
                href_item = match[0].replace(' ', '+')
                href = f'/{server_arg}/{realm_arg}?search={href_item}&time={time_arg}'
                item_suggestions.append((href_display, href))
            return render_template(html_page, title=tab,
                                   AH_title=AH_title,
                                   suggestions=item_suggestions,
                                   value=search_arg, tvalue=time_arg)
        else:
            return render_template(html_page, title='Item not found',
                                   AH_title=AH_title,
                                   error='Item was not found in the database',
                                   value=search_arg, tvalue=time_arg)
    
    match = re.match('((.+?_(A|H))).+', realm_arg)
    short = match.group(1)
    sql = (f"""SELECT itemname, price, scantime FROM {short}_prices
        INNER JOIN WOTLK_items ON {short}_prices.itemid=WOTLK_items.itemid 
        INNER JOIN WOTLK_scans ON {short}_prices.scanid=WOTLK_scans.scanid 
        WHERE WOTLK_items.itemname IS ? 
        AND WOTLK_scans.scantime > ?;""")
    cur.execute(sql, query)
    datapoints = sorted(cur.fetchall(), key=lambda x: x[2])
    if not datapoints:
        msg = "This item has not been listed on the auction house in the selected time range."
        return render_template(html_page, title='No prices available',
                               AH_title=AH_title, error=msg,
                               value=search_arg, tvalue=time_arg)
    '''
    # Calculate MAD and remove outliers
    if len(datapoints) > 21:
        outliers = []
        imax = len(datapoints) - 1
        for i, point in enumerate(datapoints):
            price = point[1]
            if i - 10 < 0:
                indexes = range(21)
            elif i + 10 > imax:
                indexes = range(imax-20, imax+1)
            else:
                indexes = range(i-10, i+11)
            prices = [datapoints[i][1] for i in indexes]
            
            median_price = (median(prices))
            diffs_median = []
            for _price in prices:
                diffs_median.append(abs(_price-median_price))
            mad = (median(diffs_median))
            #print('MAD: '+str(mad)) # debug
            #print('MED: '+str(median_price))
            #print('price: '+str(price))
            #print('prices: '+str(prices))
            if mad == 0:
                continue
            if abs(price-median_price) / mad > 20:
                outliers.append(i)
        for index in sorted(outliers, reverse=True):
            del datapoints[index]
    '''
    
    # Remove outliers
    prices = []
    outliers = []
    for i in datapoints:
        prices.append(i[1])
    prices.sort()
    high_price = 1.5 * prices[int(0.95 * len(prices))]
    for i, point in enumerate(datapoints):
        price = point[1]
        if price > high_price:
            outliers.append(i)
    for index in sorted(outliers, reverse=True):
        del datapoints[index]
    
    # Format data from query
    item = datapoints[0][0]
    time_list = []
    price_list = []
    for i in datapoints:
        time_list.append(i[2])
        price_list.append(i[1])
    # Generate moving average
    window = '5D'
    index = pd.to_datetime(time_list, unit = 's')
    df = pd.DataFrame({'prices': price_list}, index)
    dfr = df.rolling(window).mean()
    # Create traces
    trace_price = plotgo.Scattergl(
        x = index,
        y = price_list,
        text = list(map(copper_to_price, price_list)),
        hoverinfo = 'text+x',
        name = item,
        mode = 'markers',)
    
    trace_avg = plotgo.Scattergl(
        x = dfr.axes[0],
        y = dfr['prices'],
        text = list(map(copper_to_price, dfr['prices'])),
        hoverinfo = 'text+x',
        hoverlabel = dict(bordercolor = '#ffffff',
            font = dict(color = '#ffffff')),
        name = 'average ({window})'.format(window = window),
        mode = 'lines',)
    plotdata = [trace_price, trace_avg]
    # Layout
    max_val = max(price_list)
    nr_ticks = 6
    y_val = 0
    y_vals = []
    
    _t1 = (max_val / nr_ticks)
    _t2 = int(np.log10(_t1))
    _t3 = int(_t1 / (10**_t2)) + 1
    step = _t3 * 10**_t2
    for i in range(nr_ticks):
        y_val += step
        y_vals.append(y_val)
    
    layout = plotgo.Layout(
        title = "{item}'s price history".format(item=item),
        font = dict(
            color = '#ffffff'    
        ),
        yaxis=dict(
            gridcolor='rgba(26, 26, 26, 0.6)',
            tickvals = y_vals,
            ticktext = list(map(copper_to_price, y_vals))
        ),
        xaxis = dict(
            gridcolor='rgba(26, 26, 26, 0.2)',
            hoverformat = '%e %b %Y'
        ),
        paper_bgcolor='#263238',
        plot_bgcolor='#263238'
    )

    fig = dict(data=plotdata, layout=layout)
    chart = plotly.offline.plot(fig, include_plotlyjs=False, output_type="div")
    write_log(realm_arg, item, 'graph', time_arg)
    return render_template(html_page, title=tab, AH_title=AH_title,
                           chart=chart, value=search_arg, tvalue=time_arg)


